# Copyright (c) 2015-2020 by the parties listed in the AUTHORS file.
# All rights reserved.  Use of this source code is governed by
# a BSD-style license that can be found in the LICENSE file.

import jax
import jax.numpy as jnp

from ....utils import Logger

from ....jax.mutableArray import MutableJaxArray
from ....jax.intervals import INTERVALS_JAX, JaxIntervals, ALL


def global_to_local(global_pixels, npix_submap, global2local):
    """
    Convert global pixel indices to local submaps and pixels within the submap.

    Args:
        global_pixels (array):  The global pixel indices (size nsamples).
        npix_submap (int):  The number of pixels in each submap.
        global2local (array, int64):  The local submap for each global submap.

    Returns:
        (tuple of array):  The (local submap, pixel within submap) for each global pixel (each of size nsamples).
    """
    quotient, remainder = jnp.divmod(global_pixels, npix_submap)
    local_pixels = jnp.where(global_pixels < 0, -1, remainder)
    local_submaps = jnp.where(global_pixels < 0, -1, global2local[quotient])
    return (local_submaps, local_pixels)


def scan_map_inner(
    mapdata,
    npix_submap,
    global2local,
    pixels,
    weights,
    det_data,
    data_scale,
    should_zero,
    should_subtract,
):
    """
    Applies scan_map to a given interval.

    Args:
        mapdata (array, ?):  The local piece of the map (size ?*npix_submap*nmap).
        npix_submap (int):  The number of pixels in each submap.
        global2local (array, int64):  The local submap for each global submap.
        pixels (array, int): pixels (size nsample)
        weights (array, float64):  The pointing matrix weights (size: nsample*nmap).
        det_data (array, float64):  The timestream on which to accumulate the map values (size nsample).
        data_scale (float): unit rescaling
        should_zero (bool): should we zero det_data
        should_subtract (bool): should we subtract from det_data

    Returns:
        det_data
    """
    # Get local submap and pixels
    submap, subpix = global_to_local(pixels, npix_submap, global2local)

    # gets the local mapdata
    # zero-out samples with invalid indices
    # by default JAX will put any value where the indices were invalid instead of erroring out
    valid_samples = (subpix >= 0) & (submap >= 0)
    mapdata = jnp.where(valid_samples[:, jnp.newaxis], mapdata[submap, subpix, :], 0.0)

    # computes the update term
    update = jnp.sum(mapdata * weights, axis=1) * data_scale

    # updates det_data and returns
    if should_zero:
        det_data = jnp.zeros_like(det_data)
    return (det_data - update) if should_subtract else (det_data + update)


# maps over intervals and detectors
# scan_map_inner = jax_xmap(scan_map_inner,
#                              in_axes=[[...], # mapdata
#                                       [...], # npix_submap
#                                       [...], # global2local
#                                       ['detectors','intervals',...], # pixels
#                                       ['detectors','intervals',...], # weights
#                                       ['detectors','intervals',...], # det_data
#                                       [...], # data_scale
#                                       [...], # should_zero
#                                       [...]], # should_subtract
#                              out_axes=['detectors','intervals',...])
# TODO xmap is commented out for now due to a [bug with static argnum](https://github.com/google/jax/issues/10741)
scan_map_inner = jax.vmap(
    scan_map_inner,
    in_axes=[None, None, None, 0, 0, 0, None, None, None],
    out_axes=0,
)  # loop on intervals
scan_map_inner = jax.vmap(
    scan_map_inner,
    in_axes=[None, None, None, 0, 0, 0, None, None, None],
    out_axes=0,
)  # loop on detectors


def scan_map_interval(
    mapdata,
    nmap,
    npix_submap,
    global2local,
    det_data,
    det_data_index,
    pixels,
    pixels_index,
    weights,
    weight_index,
    interval_starts,
    interval_ends,
    intervals_max_length,
    data_scale,
    should_zero,
    should_subtract,
):
    """
    Process all the intervals as a block.

    Args:
        mapdata (array, ?):  The local piece of the map (size ?*npix_submap*nmap).
        nmap (int): number of valid pixels
        npix_submap (int):  The number of pixels in each submap.
        global2local (array, int64):  The local submap for each global submap.
        det_data (array, float): size ???*n_samp
        det_data_index (array, int): The indexes of the det_data (size n_det)
        pixels (array, int): pixels (size ???*n_samp)
        pixels_index (array, int): The indexes of the pixels (size n_det)
        weights (optional array, float64): The flat packed detectors weights for the specified mode (size ???*n_samp*3)
        weight_index (optional array, int): The indexes of the weights (size n_det)
        interval_starts (array, int): size n_view
        interval_ends (array, int): size n_view
        intervals_max_length (int): maximum length of an interval
        data_scale (float): unit scaling
        should_zero (bool): should we zero det_data
        should_subtract (bool): should we subtract from det_data

    Returns:
        det_data (array, float): size ???*n_samp
    """
    # debugging information
    log = Logger.get()
    log.debug(f"scan_map: jit-compiling.")

    # turns mapdata into a numpy array of shape ?*npix_submap*nmap
    mapdata = jnp.reshape(mapdata, newshape=(-1, npix_submap, nmap))

    # extract interval slices
    intervals = JaxIntervals(
        interval_starts, interval_ends + 1, intervals_max_length
    )  # end+1 as the interval is inclusive
    pixels_interval = JaxIntervals.get(
        pixels, (pixels_index, intervals)
    )  # pixels[pixels_index, intervals]
    det_data_interval = JaxIntervals.get(
        det_data, (det_data_index, intervals)
    )  # det_data[det_data_index, intervals]
    if weight_index is None:
        weights_interval = jnp.ones_like(pixels_interval)
    elif weights.ndim == 2:
        weights_interval = JaxIntervals.get(
            weights, (weight_index, intervals)
        )  # weights[weight_index, intervals]
    else:
        weights_interval = JaxIntervals.get(
            weights, (weight_index, intervals, ALL)
        )  # weights[weight_index, intervals, :]

    # does the computation
    new_det_data_interval = scan_map_inner(
        mapdata,
        npix_submap,
        global2local,
        pixels_interval,
        weights_interval,
        det_data_interval,
        data_scale,
        should_zero,
        should_subtract,
    )

    # updates results and returns
    # det_data[det_data_index, intervals] = new_det_data_interval
    det_data = JaxIntervals.set(
        det_data, (det_data_index, intervals), new_det_data_interval
    )
    return det_data


# jit compiling
scan_map_interval = jax.jit(
    scan_map_interval,
    static_argnames=[
        "nmap",
        "npix_submap",
        "intervals_max_length",
        "should_zero",
        "should_subtract",
    ],
    donate_argnums=[4],
)  # donates det_data


def scan_map(
    mapdata,
    nmap,
    det_data,
    det_data_index,
    pixels,
    pixels_index,
    weights,
    weight_index,
    intervals,
    map_dist,
    data_scale,
    should_zero,
    should_subtract,
    use_accel,
):
    """
    Sample a map into a timestream.

    This uses a distributed map and the pointing matrix
    to generate timestream values.

    Args:
        mapdata (Pixels):  The local piece of the map.
        nmap (int): number of valid pixels
        det_data (array, float): size ???*n_samp
        det_data_index (array, int): The indexes of the det_data (size n_det)
        pixels (array, int): pixels (size ???*n_samp)
        pixels_index (array, int): The indexes of the pixels (size n_det)
        weights (optional array, float64): The flat packed detectors weights for the specified mode (size ???*n_samp*3)
        weight_index (optional array, int): The indexes of the weights (size n_det)
        intervals (array, Interval): The intervals to modify (size n_view)
        map_dist (PixelDistribution): encapsulate information to translate the pixel mapping
        data_scale (float): unit scalling
        should_zero (bool): should we zero det_data
        should_subtract (bool): should we subtract from det_data
        use_accel (bool): should we use an accelerator

    Returns:
        None: det_data is updated in place
    """
    # extracts pixel distribution information
    npix_submap = map_dist._n_pix_submap
    global2local = map_dist._glob2loc

    # prepares inputs
    intervals_max_length = INTERVALS_JAX.compute_max_intervals_length(intervals)
    mapdata = MutableJaxArray.to_array(mapdata.data)
    global2local = MutableJaxArray.to_array(global2local)
    det_data_input = MutableJaxArray.to_array(det_data)
    det_data_index = MutableJaxArray.to_array(det_data_index)
    pixels = MutableJaxArray.to_array(pixels)
    pixels_index = MutableJaxArray.to_array(pixels_index)
    weights = MutableJaxArray.to_array(weights)
    weight_index = MutableJaxArray.to_array(weight_index)

    # performs computation and updates det_data in place
    det_data[:] = scan_map_interval(
        mapdata,
        nmap,
        npix_submap,
        global2local,
        det_data_input,
        det_data_index,
        pixels,
        pixels_index,
        weights,
        weight_index,
        intervals.first,
        intervals.last,
        intervals_max_length,
        data_scale,
        should_zero,
        should_subtract,
    )


# To test:
# python -c 'import toast.tests; toast.tests.run("ops_mapmaker_solve", "ops_scan_map")'