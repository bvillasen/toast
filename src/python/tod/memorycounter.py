# Copyright (c) 2015-2017 by the parties listed in the AUTHORS file.
# All rights reserved.  Use of this source code is governed by
# a BSD-style license that can be found in the LICENSE file.

import re

import numpy as np

from ..mpi import MPI
from ..op import Operator
from ..dist import Comm, Data
from .tod import TOD
from .. import rng as rng


class OpMemoryCounter(Operator):
    """
    Operator which loops over the TOD objects and computes the total
    amount of memory allocated.

    Args:
        silent (bool):  Only count and return the memory without
            printing.
        *other_caching_objects:  Additional objects that have a cache
            member and user wants to include in the total counts
            (e.q. DistPixels objects).
    """

    def __init__(self, *other_caching_objects, silent=False):

        self._silent = silent
        self._objects = []

        for obj in other_caching_objects:
            self._objects.append(obj)

        super().__init__()

    def exec(self, data):
        """
        Count the memory

        Args:
            data (toast.Data): The distributed data.
        """
        # the two-level pytoast communicator
        comm = data.comm
        # the global communicator
        cworld = comm.comm_world
        # the communicator within the group
        cgroup = comm.comm_group
        # the communicator with all processes with
        # the same rank within their group
        crank = comm.comm_rank

        tot_task = 0

        for obj in self._objects:
            try:
                tot_task += obj.cache.report(silent=True)
            except:
                pass
            try:
                tot_task += obj._cache.report(silent=True)
            except:
                pass

        for obs in data.obs:
            tod = obs['tod']
            tot_task += tod.cache.report(silent=True)

        tot_group = 0
        if cgroup is not MPI.UNDEFINED:
            tot_group = cgroup.allreduce(tot_task, op=MPI.SUM)
        tot_world = cworld.allreduce(tot_task, op=MPI.SUM)

        tot_task_max = cworld.allreduce(tot_task, op=MPI.MAX)
        tot_group_max = cworld.allreduce(tot_group, op=MPI.MAX)

        if cworld.rank == 0 and not self._silent:
            print()
            print('Memory usage statistics: ')
            print('- Max memory (task): {:.2f} GB'.format(
                tot_task_max / 2**30))
            print('- Max memory (group): {:.2f} GB'.format(
                tot_group_max / 2**30))
            print('Total memory: {:.2f} GB'.format(tot_world / 2**30))
            print('', flush=True)

        return tot_world
