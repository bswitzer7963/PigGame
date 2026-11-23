# Solves two-player Pig exactly (no simulation) and saves, for every pair of
# scores, the smallest turn total at which holding is the best move.
# This is separate from the tree search in pig.py; the heatmap page shows both.
#
#   python solve_exact.py            -> writes data/exact_30.json, _50, _100

import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))


def solve(target):
    # start[i][j] = chance the player to move wins, with score i against j,
    # at the start of a turn (turn total 0)
    start = [[0.0] * target for _ in range(target)]
    hold_at = [[0] * target for _ in range(target)]

    def turn_values(i, j, opp_start):
        # win chance for each turn total k, given the opponent's chance
        # (opp_start) of winning if we bust and they start a turn
        size = target - i
        value = [0.0] * size
        first_hold = size                     # "size" means: never hold, roll to the end
        for k in range(size - 1, -1, -1):
            total = 1.0 - opp_start           # rolled a 1
            for rolled in range(2, 7):
                total += 1.0 if k + rolled >= size else value[k + rolled]
            roll = total / 6.0
            if k == 0:
                value[k] = roll               # nothing to hold yet
                continue
            hold = 1.0 - start[j][i + k]
            if hold >= roll:
                value[k] = hold
                first_hold = k
            else:
                value[k] = roll
        return value[0], first_hold

    # work from the highest combined scores down, so everything a position
    # depends on (other than its mirror image) is already solved
    for combined in range(2 * target - 2, -1, -1):
        for i in range(max(0, combined - target + 1), min(target, combined + 1)):
            j = combined - i
            if i > j:
                continue
            x, y = 0.5, 0.5                   # x: (i, j) to move, y: (j, i) to move
            for _ in range(200):
                new_x, hold_x = turn_values(i, j, y)
                new_y, hold_y = turn_values(j, i, new_x)
                change = abs(new_x - x) + abs(new_y - y)
                x, y = new_x, new_y
                if change < 1e-10:
                    break
            start[i][j], hold_at[i][j] = x, hold_x
            start[j][i], hold_at[j][i] = y, hold_y

    return start, hold_at


if __name__ == "__main__":
    os.makedirs(os.path.join(HERE, "data"), exist_ok=True)
    for target in (30, 50, 100):
        start, hold_at = solve(target)
        path = os.path.join(HERE, "data", f"exact_{target}.json")
        with open(path, "w") as f:
            json.dump({"target": target, "step": 1, "grid": hold_at,
                       "first_player_wins": round(start[0][0], 4)}, f)
        print(f"target {target}: first player wins {start[0][0]:.4f} with best play -> {path}")
