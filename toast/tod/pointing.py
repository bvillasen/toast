# Copyright (c) 2015 by the parties listed in the AUTHORS file.
# All rights reserved.  Use of this source code is governed by 
# a BSD-style license that can be found in the LICENSE file.


from mpi4py import MPI

import unittest

import numpy as np

import healpy as hp

import quaternionarray as qa

from ..operator import Operator
from ..dist import Comm, Data
from .tod import TOD



class OpPointingHpix(Operator):
    """
    Operator which generates I/Q/U healpix pointing weights.

    Given the individual detector pointing, this computes the pointing weights
    assuming that the detector is a linear polarizer followed by a total
    power measurement.  An optional dictionary of calibration factors may
    be specified.  Additional options include specifying a constant cross-polar
    response (eps) and a rotating, perfect half-wave plate.  The timestream 
    model is then (see Jones, et al, 2006):

    d = cal * [ (1+eps)/2 * I + (1-eps)/2 * [Q * cos(2a) + U * sin(2a)]]

    Or, if a HWP is included in the response with time varying angle "w", then
    the total response is:

    d = cal * [ (1+eps)/2 * I + (1-eps)/2 * [Q * cos(4(a+w)) + U * sin(4(a+w))]]

    Args:
        pixels (str): write pixels to the cache with name <pixels>_<detector>.
            If the named cache objects do not exist, then they are created.
        weights (str): write pixel weights to the cache with name 
            <weights>_<detector>.  If the named cache objects do not exist, 
            then they are created. 
        nside (int): NSIDE resolution for Healpix NEST ordered intensity map.
        nest (bool): if True, use NESTED ordering.
        mode (string): either "I" or "IQU"
        cal (dict): dictionary of calibration values per detector. A None
            value means a value of 1.0 for all detectors.
        epsilon (dict): dictionary of cross-polar response per detector. A
            None value means epsilon is zero for all detectors.
        hwprpm: if None, a constantly rotating HWP is not included.  Otherwise
            it is the rate (in RPM) of constant rotation.
        hwpstep: if None, then a stepped HWP is not included.  Otherwise, this
            is the step in degrees.
        hwpsteptime: The time in minutes between HWP steps.
    """

    def __init__(self, pixels='pixels', weights='pweights', nside=64, nest=False, mode='I', cal=None, epsilon=None, hwprpm=None, hwpstep=None, hwpsteptime=None):
        self._pixels = pixels
        self._pweights = pweights
        self._nside = nside
        self._nest = nest
        self._mode = mode
        self._cal = cal
        self._epsilon = epsilon
        self._purge = purge_pntg

        if (hwprpm is not None) and (hwpstep is not None):
            raise RuntimeError("choose either continuously rotating or stepped HWP")

        if (hwpstep is not None) and (hwpsteptime is None):
            raise RuntimeError("for a stepped HWP, you must specify the time between steps")

        if hwprpm is not None:
            # convert to radians / second
            self._hwprate = hwprpm * 2.0 * np.pi / 60.0
        else:
            self._hwprate = None

        if hwpstep is not None:
            # convert to radians and seconds
            self._hwpstep = hwpstep * np.pi / 180.0
            self._hwpsteptime = hwpsteptime * 60.0
        else:
            self._hwpstep = None
            self._hwpsteptime = None

        # We call the parent class constructor, which currently does nothing
        super().__init__()


    @property
    def nside(self):
        return self._nside

    @property
    def nest(self):
        return self._nest

    @property
    def mode(self):
        return self._mode


    def exec(self, data):
        # the two-level pytoast communicator
        comm = data.comm
        # the global communicator
        cworld = comm.comm_world
        # the communicator within the group
        cgroup = comm.comm_group
        # the communicator with all processes with
        # the same rank within their group
        crank = comm.comm_rank

        xaxis = np.array([1,0,0], dtype=np.float64)
        yaxis = np.array([0,1,0], dtype=np.float64)
        zaxis = np.array([0,0,1], dtype=np.float64)
        nullquat = np.array([0,0,0,1], dtype=np.float64)

        for obs in data.obs:
            tod = obs['tod']

            # compute effective sample rate

            times = tod.read_times(local_start=0, n=tod.local_samples[1])
            dt = np.mean(times[1:-1] - times[0:-2])
            rate = 1.0 / dt

            # generate HWP angles

            nsamp = tod.local_samples[1]
            first = tod.local_samples[0]
            hwpang = None

            if self._hwprate is not None:
                # continuous HWP
                # HWP increment per sample is: 
                # (hwprate / samplerate)
                hwpincr = self._hwprate / rate
                startang = np.fmod(first * hwpincr, 2*np.pi)
                hwpang = hwpincr * np.arange(nsamp, dtype=np.float64)
                hwpang += startang
            elif self._hwpstep is not None:
                # stepped HWP
                hwpang = np.ones(nsamp, dtype=np.float64)
                stepsamples = int(self._hwpsteptime * rate)
                wholesteps = int(first / stepsamples)
                remsamples = first - wholesteps * stepsamples
                curang = np.fmod(wholesteps * self._hwpstep, 2*np.pi)
                curoff = 0
                fill = remsamples
                while (curoff < nsamp):
                    if curoff + fill > nsamp:
                        fill = nsamp - curoff
                    hwpang[curoff:fill] *= curang
                    curang += self._hwpstep
                    curoff += fill
                    fill = stepsamples

            for det in tod.local_dets:

                eps = 0.0
                if self._epsilon is not None:
                    eps = self._epsilon[det]

                cal = 1.0
                if self._cal is not None:
                    cal = self._cal[det]

                oneplus = 0.5 * (1.0 + eps)
                oneminus = 0.5 * (1.0 - eps)

                pdata = np.copy(tod.read_pntg(detector=det, local_start=0, n=nsamp))
                flags, common = tod.read_flags(detector=det, local_start=0, n=nsamp)
                totflags = np.copy(flags)
                totflags |= common

                pdata[totflags != 0,:] = nullquat

                dir = qa.rotate(pdata, np.tile(zaxis, nsamp).reshape(-1,3))

                pixels = hp.vec2pix(self._nside, dir[:,0], dir[:,1], dir[:,2], nest=self._nest)
                pixels[totflags != 0] = -1

                if self._mode == 'I':
                    
                    weights = np.ones(nsamp, dtype=np.float64)
                    weights *= (cal * oneplus)

                elif self._mode == 'IQU':

                    orient = qa.rotate(pdata.reshape(-1, 4), np.tile(xaxis, nsamp).reshape(-1,3))

                    by = orient[:,0] * dir[:,1] - orient[:,1] * dir[:,0]
                    bx = orient[:,0] * (-dir[:,2] * dir[:,0]) + orient[:,1] * (-dir[:,2] * dir[:,1]) + orient[:,2] * (dir[:,0] * dir[:,0] + dir[:,1] * dir[:,1])
                        
                    detang = np.arctan2(by, bx)
                    if hwpang is not None:
                        detang += hwpang
                        detang *= 4.0
                    else:
                        detang *= 2.0
                    cang = np.cos(detang)
                    sang = np.sin(detang)
                     
                    Ival = np.ones_like(cang)
                    Ival *= (cal * oneplus)
                    Qval = cang
                    Qval *= (cal * oneminus)
                    Uval = sang
                    Uval *= (cal * oneminus)

                    weights = np.ravel(np.column_stack((Ival, Qval, Uval))).reshape(-1,3)

                else:
                    raise RuntimeError("invalid mode for healpix pointing")

                pixelsname = "{}_{}".format(self._pixels, det)
                weightsname = "{}_{}".format(self._weights, det)
                if not tod.cache.exists(pixelsname):
                    tod.cache.create(pixelsname, np.float64, (tod.local_samples[1],))
                if not tod.cache.exists(pixelsname):
                    tod.cache.create(pixelsname, np.float64, (tod.local_samples[1],weights.shape[1]))
                pixelsref = tod.cache.reference(pixelsname)
                weightsref = tod.cache.reference(weightsname)
                pixelsref[:] = pixels
                weightsref[:,:] = weights

        return

