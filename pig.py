# Pig game state and Monte Carlo tree search.
import math
import random

WINNING_AMOUNT = 30


class GameState:
    def __init__(self, scores=[0, 0], cur_player=0, cur_tot=0, win_score=WINNING_AMOUNT):
        self.num_players = len(scores)
        self.scores = list(scores)
        self.win_score = win_score
        self.cur_player = cur_player
        self.cur_tot = cur_tot

    def next_player(self):
        return (self.cur_player + 1) % self.num_players

    def get_state(self):
        return [self.scores, self.cur_player, self.cur_tot]

    def action_options(self):
        if (self.scores[self.cur_player] + self.cur_tot) >= self.win_score:
            return ["hold"]
        elif self.cur_tot <= 0:
            return ["roll"]
        else:
            return ["hold", "roll"]

    def make_action(self, choice):
        if choice not in self.action_options():
            return None
        else:
            if choice == "hold":
                return self.hold_reaction()
            else:
                return self.roll_reaction(self.roll_dice())

    def roll_dice(self):
        return random.randint(1, 6)

    def roll_reaction(self, rolled):
        if rolled == 1:
            return GameState(self.scores, self.next_player(), 0, self.win_score)
        else:
            return GameState(self.scores, self.cur_player, (self.cur_tot + rolled), self.win_score)

    def hold_reaction(self):
        new_scores = self.scores.copy()
        new_scores[self.cur_player] += self.cur_tot
        return GameState(new_scores, self.next_player(), 0, self.win_score)

    def is_won(self):
        for player in range(self.num_players):
            if self.scores[player] >= self.win_score:
                return player
        return None


class GameNode:
    def __init__(self, state, parent=None, action=None, roll=None):
        self.state = state
        self.action = action
        self.parent = parent
        self.roll = roll
        self.children = []
        self.visits = 0
        self.wins = 0


    def existing_vals(self, action):
        visits = 0
        wins = 0
        for child in self.children:
            if child.action == action:
                visits += child.visits
                wins += child.wins
        return visits, wins


    def pick_action(self, c=2):
        max_act = None
        max_val = -1000000

        for action in self.state.action_options():
            visits, wins = self.existing_vals(action)

            if visits == 0:
                return action

            val = (wins / visits) + c * (math.sqrt(math.log(self.visits) / visits))

            if val > max_val:
                max_val = val
                max_act = action

        return max_act

    def do_action(self, action):
        if action == "roll":
            roll = self.state.roll_dice()
            new_state = self.state.roll_reaction(roll)
        elif action == "hold":
            roll = None
            new_state = self.state.hold_reaction()
        else:
            raise ValueError("Doom and despair")

        for child in self.children:
            if child.action == action and child.roll == roll:
                return child, False

        child = GameNode(new_state, self, action, roll)
        self.children.append(child)

        return child, True

    def rollout(self, hold_top=20):
        state = self.state
        while state.is_won() is None:
            if "roll" in state.action_options() and state.cur_tot < hold_top:
                state = state.make_action("roll")
            else:
                state = state.make_action("hold")

        return state.is_won()


def sim(root, hold_top=20):
    node = root
    while node.state.is_won() is None:
        node, is_child = node.do_action(node.pick_action())
        if is_child:
            break

    which_won = node.rollout(hold_top)

    #add sims and visits to path finally
    while node is not None:
        node.visits += 1
        if node.parent is not None and which_won == node.parent.state.cur_player:
            node.wins += 1
        node = node.parent

def ai_make_choice(state, tot_sims):

    if len(state.action_options()) == 1:
        return state.action_options()[0]

    root = GameNode(state)
    for _ in range(tot_sims):
        sim(root)

    #just pick action w/o explore bonus
    max_act = None
    max_val = -1000000

    for action in state.action_options():
        visits, wins = root.existing_vals(action)

        if visits > max_val:
            max_val = visits
            max_act = action

    return max_act
