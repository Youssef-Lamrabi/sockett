#!/bin/bash
#SBATCH --job-name=batch1_genomeer
#SBATCH --output=logs/judge_%j.out
#SBATCH --error=logs/judge_%j.err
#SBATCH --time=336:00:00
#SBATCH --cpus-per-task=4
#SBATCH --mem=16G
#SBATCH --partition=all
##SBATCH --constraint='!nvidia_5'
##SBATCH --nodelist=worker-01 #master-03 #worker-01

set -x

# ---------------------------------
# LOAD ENVIRONMENT
# ---------------------------------
unset LD_PRELOAD 
export CONDA_PREFIX=/mnt/nfs/llmhub/torch_271py_core
export PATH=$CONDA_PREFIX/bin:$PATH
export LD_LIBRARY_PATH=$CONDA_PREFIX/lib:$LD_LIBRARY_PATH

# ---------------------------------
# MOVE TO WORK DIR
# ---------------------------------
#cd /mnt/nfs/llmhub/Genomeer/dataset/06-quality-check

# ---------------------------------
# RUN SCRIPT
# ---------------------------------
python run_judge_v3_parallele-batch1.py
