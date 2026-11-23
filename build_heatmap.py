# Builds the tree-search side of the strategy heatmap using pig.py.
# For a grid of score pairs it searches for the smallest turn total at which
# the tree search prefers to hold, and saves the result for the heatmap page.
#
#   python build_heatmap.py                 -> all three targets, default settings
#   python build_heatmap.py 100 5 4000      -> target 100, every 5 points, 4000 sims per decision
#
# More sims per decision gives a smoother map and takes proportionally longer.

import json
import os
import sys
import time
from multiprocessing import Pool

from pig import GameState, GameNode, sim

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULTS = {30: (2, 4000), 50: (2, 4000), 100: (5, 4000)}     # target: (grid step, sims)


def prefers_hold(my_score, their_score, turn_total, target, sims):
    root = GameNode(GameState([my_score, their_score], 0, turn_total, target))
    for _ in range(sims):
        sim(root)
    return root.existing_vals("hold")[0] > root.existing_vals("roll")[0]


def first_hold(job):
    my_score, their_score, target, sims = job
    # search turn totals 1 .. (points still needed - 1) for where roll turns into hold
    low, high = 1, target - my_score          # "high" means: never holds
    while low < high:
        middle = (low + high) // 2
        if prefers_hold(my_score, their_score, middle, target, sims):
            high = middle
        else:
            low = middle + 1
    return low


def build(target, step, sims):
    scores = list(range(0, target, step))
    jobs = [(i, j, target, sims) for i in scores for j in scores]
    started = time.time()
    with Pool() as pool:
        flat = pool.map(first_hold, jobs, chunksize=4)
    grid = [flat[r * len(scores):(r + 1) * len(scores)] for r in range(len(scores))]
    path = os.path.join(HERE, "data", f"mcts_{target}.json")
    with open(path, "w") as f:
        json.dump({"target": target, "step": step, "sims": sims, "grid": grid}, f)
    print(f"target {target}: {len(jobs)} score pairs in {time.time() - started:.0f}s -> {path}")


if __name__ == "__main__":
    os.makedirs(os.path.join(HERE, "data"), exist_ok=True)
    if len(sys.argv) >= 2:
        target = int(sys.argv[1])
        step = int(sys.argv[2]) if len(sys.argv) >= 3 else DEFAULTS.get(target, (5, 4000))[0]
        sims = int(sys.argv[3]) if len(sys.argv) >= 4 else DEFAULTS.get(target, (5, 4000))[1]
        build(target, step, sims)
    else:
        for target, (step, sims) in DEFAULTS.items():
            build(target, step, sims)
