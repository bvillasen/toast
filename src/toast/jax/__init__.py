# NOTE:
#
# to run miniapp:
# TOAST_GPU_JAX=true TOAST_LOGLEVEL=DEBUG toast_mini --node_mem_gb 4.0
# export TOAST_GPU_JAX=false; export JAX_PLATFORM_NAME=gpu; export OMP_NUM_THREADS=32; timer toast_mini --node_mem_gb 4.0
# TOAST_GPU_JAX=false JAX_PLATFORM_NAME=gpu nsys profile --stats=true toast_mini --node_mem_gb 4.0
#
# - get rid of the self.use_python versions (we have the numpy ones for test purposes)
#
# - the code uses [xmap](https://jax.readthedocs.io/en/latest/notebooks/xmap_tutorial.html)
# in order to map over named axis for increased readability
# however, one could use several vmap to reproduce this functionality

# enable 64bits precision
from jax.config import config as jax_config
jax_config.update("jax_enable_x64", True)