# Copyright (c) 2015-2020 by the parties listed in the AUTHORS file.
# All rights reserved.  Use of this source code is governed by
# a BSD-style license that can be found in the LICENSE file.

import numpy as np

import traitlets

from ..utils import Environment, Logger, memreport

from ..timing import function_timer, Timer

from ..noise_sim import AnalyticNoise

from ..traits import trait_docs, Int, Bool, Unicode

from .operator import Operator


@trait_docs
class MemoryCounter(Operator):
    """Compute total memory used by Observations in a Data object.

    Every process group iterates over their observations and sums the total memory used
    by detector and shared data.  Metadata and interval lists are assumed to be
    negligible and are not counted.

    """

    # Class traits

    API = Int(0, help="Internal interface version for this operator")

    silent = Bool(
        False,
        help="If True, return the memory used but do not log the result",
    )

    prefix = Unicode("", help="Prefix for log messages")

    def __init__(self, **kwargs):
        self.total_bytes = 0
        super().__init__(**kwargs)

    def _exec(self, data, detectors=None, **kwargs):
        for ob in data.obs:
            self.total_bytes += ob.memory_use()
        self.sys_mem_str = memreport(
            msg="(whole node)", comm=data.comm.comm_world, silent=True
        )
        return

    def _finalize(self, data, **kwargs):
        log = Logger.get()
        if not self.silent:
            if data.comm.world_rank == 0:
                msg = "Total timestream memory use = {:0.2f} GB".format(
                    self.total_bytes / 1024 ** 3
                )
                log.info(f"{self.prefix}:  {msg}")
                log.info(f"{self.prefix}:  {self.sys_mem_str}")
        total = self.total_bytes
        self.total_bytes = 0
        return total

    def _requires(self):
        return dict()

    def _provides(self):
        return dict()

    def _accelerators(self):
        return list()