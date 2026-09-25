"""Server-authoritative no-limit Texas Hold'em engine (amounts are whole yuan)."""
from __future__ import annotations

import itertools
import random
from dataclasses import dataclass, field

SMALL_BLIND = 5
BIG_BLIND = 10
MAX_PLAYERS = 10
RANK_NAMES = ["高牌", "一对", "两对", "三条", "顺子", "同花", "葫芦", "四条", "同花顺"]


def deck():
    cards = [(rank, suit) for suit in "shdc" for rank in range(2, 15)]
    random.SystemRandom().shuffle(cards)
    return cards


def card_text(card):
    return "23456789TJQKA"[card[0] - 2] + card[1]


def score_five(cards):
    ranks = sorted((r for r, _ in cards), reverse=True)
    counts = {r: ranks.count(r) for r in set(ranks)}
    groups = sorted(((n, r) for r, n in counts.items()), reverse=True)
    flush = len({s for _, s in cards}) == 1
    unique = set(ranks)
    if 14 in unique:
        unique.add(1)
    straight = next((hi for hi in range(14, 4, -1) if all(hi - d in unique for d in range(5))), 0)
    if flush and straight:
        return (8, straight)
    if groups[0][0] == 4:
        return (7, groups[0][1], groups[1][1])
    if groups[0][0] == 3 and groups[1][0] == 2:
        return (6, groups[0][1], groups[1][1])
    if flush:
        return (5, *ranks)
    if straight:
        return (4, straight)
    if groups[0][0] == 3:
        return (3, groups[0][1], *sorted((r for r in ranks if r != groups[0][1]), reverse=True))
    if groups[0][0] == 2 and groups[1][0] == 2:
        pairs = sorted((r for n, r in groups if n == 2), reverse=True)
        return (2, *pairs, max(r for r in ranks if r not in pairs))
    if groups[0][0] == 2:
        return (1, groups[0][1], *sorted((r for r in ranks if r != groups[0][1]), reverse=True))
    return (0, *ranks)


def score(cards):
    return max(score_five(c) for c in itertools.combinations(cards, 5))


def best_five(cards):
    """Return the five actual cards used for the highest ranked hand."""
    return max(itertools.combinations(cards, 5), key=score_five)


@dataclass
class Player:
    id: str
    name: str
    stack: int
    buyin: int
    bot: bool = False
    connected: bool = True
    cards: list = field(default_factory=list)
    street_bet: int = 0
    total_bet: int = 0
    folded: bool = False
    all_in: bool = False
    in_hand: bool = False
    acted: bool = False
    last_action_bet: int = 0
    last_action: str = ""
    action_seq: int = 0
    departed: bool = False
    pending_buyin: int = 0


class Table:
    def __init__(self):
        self.players: list[Player] = []
        self.dealer = -1
        self.phase = "waiting"
        self.board = []
        self.pile = []
        self.turn: str | None = None
        self.current_bet = 0
        self.last_full_raise = BIG_BLIND
        self.hand_no = 0
        self.result = None
        self.events = []
        self.action_seq = 0

    def add(self, player):
        if sum(not p.departed for p in self.players) >= MAX_PLAYERS:
            raise ValueError("房间已满（最多 10 人）")
        self.players.append(player)

    def prune_departed(self):
        if not any(p.departed for p in self.players):
            return
        old_dealer = self.dealer
        removed_before = sum(p.departed for p in self.players[:old_dealer + 1])
        self.players = [p for p in self.players if not p.departed]
        self.dealer = (old_dealer - removed_before) % len(self.players) if self.players else -1

    def remove(self, player_id, label="离开房间"):
        p = next((p for p in self.players if p.id == player_id and not p.departed), None)
        if p is None:
            raise ValueError("玩家不在房间内")
        p.departed = True
        p.connected = False
        self.events.append(f"{p.name} {label}")
        active = self.phase not in ("waiting", "complete")
        if active and p.in_hand:
            p.folded = True
            p.last_action = label
            if self.turn == p.id:
                self.turn = None
                self.advance(self.players.index(p))
            elif len(self.live()) == 1:
                self.settle()
        if self.phase in ("waiting", "complete"):
            self.prune_departed()

    def eligible(self):
        return [p for p in self.players if not p.departed and p.stack > 0 and (p.bot or p.connected)]

    def next_index(self, index, predicate):
        for offset in range(1, len(self.players) + 1):
            i = (index + offset) % len(self.players)
            if predicate(self.players[i]):
                return i
        return None

    def start(self):
        if self.phase not in ("waiting", "complete"):
            raise ValueError("当前牌局尚未结束")
        self.prune_departed()
        ready = self.eligible()
        if len(ready) < 2:
            raise ValueError("至少需要两名有筹码的玩家")
        self.hand_no += 1
        self.phase = "preflop"
        self.board = []
        self.pile = deck()
        self.result = None
        self.events.append(f"第 {self.hand_no} 局开始")
        self.action_seq = 0
        self.dealer = self.next_index(self.dealer, lambda p: p in ready)
        for p in self.players:
            p.cards = []
            p.street_bet = p.total_bet = 0
            p.folded = p.all_in = p.acted = False
            p.last_action_bet = 0
            p.last_action = ""
            p.action_seq = 0
            p.in_hand = p in ready
        # Deal one card at a time, starting left of the button.
        for _ in range(2):
            for offset in range(1, len(self.players) + 1):
                p = self.players[(self.dealer + offset) % len(self.players)]
                if p.in_hand:
                    p.cards.append(self.pile.pop())
        if len(ready) == 2:
            sb_i = self.dealer
        else:
            sb_i = self.next_index(self.dealer, lambda p: p.in_hand)
        bb_i = self.next_index(sb_i, lambda p: p.in_hand)
        self.post(self.players[sb_i], SMALL_BLIND, "小盲")
        self.post(self.players[bb_i], BIG_BLIND, "大盲")
        self.current_bet = BIG_BLIND  # Short big blind does not lower the call amount.
        self.last_full_raise = BIG_BLIND
        self.turn = None
        self.advance(bb_i)
        return self

    def post(self, p, amount, label):
        paid = min(p.stack, amount)
        p.stack -= paid
        p.street_bet += paid
        p.total_bet += paid
        p.all_in = p.stack == 0
        p.last_action = f"{label} ¥{paid}"
        if label in ("小盲", "大盲"):
            self.action_seq += 1
            p.action_seq = self.action_seq
            self.events.append(f"{p.name} 投入{label} ¥{paid}")

    def live(self):
        return [p for p in self.players if p.in_hand and not p.folded]

    def needs_action(self, p):
        return p.in_hand and not p.folded and not p.all_in and (not p.acted or p.street_bet < self.current_bet)

    def options(self, p):
        if p.id != self.turn or not self.needs_action(p):
            return None
        call = max(0, self.current_bet - p.street_bet)
        can_raise = (not p.acted or self.current_bet - p.last_action_bet >= self.last_full_raise)
        min_to = self.current_bet + self.last_full_raise if self.current_bet else BIG_BLIND
        max_to = p.street_bet + p.stack
        return {"call": min(call, p.stack), "check": call == 0,
                "canRaise": can_raise and max_to > self.current_bet,
                "minTo": min_to, "maxTo": max_to,
                "shortAllIn": max_to > self.current_bet and max_to < min_to}

    def act(self, player_id, action, target=None):
        p = next((p for p in self.players if p.id == player_id), None)
        if p is None or p.id != self.turn:
            raise ValueError("现在不是你的回合")
        opt = self.options(p)
        if opt is None:
            raise ValueError("当前不能行动")
        before = self.current_bet
        if action == "fold":
            p.folded = True
            p.last_action = "弃牌"
        elif action == "call":
            self.post(p, opt["call"], "跟注" if opt["call"] else "过牌")
            p.last_action = f"跟注 ¥{opt['call']}" if opt["call"] else "过牌"
        elif action == "raise":
            if not opt["canRaise"]:
                raise ValueError("当前不能加注")
            if type(target) is not int or target % 5 or target <= before or target > opt["maxTo"]:
                raise ValueError("加注额须为 5 的倍数且不能超过持有筹码")
            if target < opt["minTo"] and target != opt["maxTo"]:
                raise ValueError(f"最低加注至 ¥{opt['minTo']}，不足时可全下")
            self.post(p, target - p.street_bet, "加注")
            increase = target - before
            self.current_bet = target
            if increase >= self.last_full_raise:
                self.last_full_raise = increase
                for other in self.players:
                    if other is not p and other.in_hand and not other.folded:
                        other.acted = False
            p.last_action = f"加注至 ¥{target}" if not p.all_in else f"全下 ¥{target}"
        else:
            raise ValueError("无效操作")
        p.acted = True
        p.last_action_bet = self.current_bet
        self.events.append(f"{p.name} {p.last_action}")
        self.action_seq += 1
        p.action_seq = self.action_seq
        self.turn = None
        self.advance(self.players.index(p))

    def advance(self, after_index):
        if len(self.live()) == 1:
            self.settle()
            return
        i = self.next_index(after_index, self.needs_action)
        if i is not None:
            self.turn = self.players[i].id
            return
        self.next_street()

    def next_street(self):
        while True:
            if self.phase == "river":
                self.settle()
                return
            self.pile.pop()  # burn
            if self.phase == "preflop":
                self.board += [self.pile.pop() for _ in range(3)]
                self.phase = "flop"
            elif self.phase == "flop":
                self.board.append(self.pile.pop())
                self.phase = "turn"
            else:
                self.board.append(self.pile.pop())
                self.phase = "river"
            street = {"flop": "翻牌", "turn": "转牌", "river": "河牌"}[self.phase]
            shown = self.board if self.phase == "flop" else self.board[-1:]
            self.events.append(f"{street}：{' '.join(card_text(c) for c in shown)}")
            for p in self.players:
                p.street_bet = 0
                p.acted = False
                p.last_action_bet = 0
            self.current_bet = 0
            self.last_full_raise = BIG_BLIND
            i = self.next_index(self.dealer, self.needs_action)
            if i is not None and sum(p.in_hand and not p.folded and not p.all_in for p in self.players) > 1:
                self.turn = self.players[i].id
                return

    def settle(self):
        contenders = self.live()
        best_hands = {p.id: best_five(p.cards + self.board) for p in contenders
                      if len(p.cards) + len(self.board) >= 5}
        scores = {pid: score_five(cards) for pid, cards in best_hands.items()}
        levels = sorted({p.total_bet for p in self.players if p.total_bet})
        previous = 0
        awards = {p.id: 0 for p in self.players}
        pots = []
        for level in levels:
            contributors = [p for p in self.players if p.total_bet >= level]
            amount = (level - previous) * len(contributors)
            possible = [p for p in contributors if not p.folded]
            if not possible:
                # Can only occur with an unmatched folded bet; refund to the last contributor.
                possible = [contributors[-1]]
            best = max((scores.get(p.id, ()) for p in possible), default=())
            winners = [p for p in possible if scores.get(p.id, ()) == best]
            # Odd chips go to the first winning seat clockwise from the dealer.
            winners.sort(key=lambda p: (self.players.index(p) - self.dealer) % len(self.players))
            share, odd = divmod(amount // 5, len(winners))
            for n, p in enumerate(winners):
                gain = 5 * (share + (n < odd))
                p.stack += gain
                awards[p.id] += gain
            pots.append({"amount": amount, "winners": [p.id for p in winners]})
            previous = level
        self.phase = "complete"
        self.turn = None
        self.result = {"pots": pots, "awards": awards,
                       "hands": {p.id: RANK_NAMES[scores[p.id][0]] if p.id in scores else "未摊牌"
                                 for p in contenders},
                       "bestCards": {pid: [card_text(c) for c in cards] for pid, cards in best_hands.items()}}
        for p in contenders:
            self.events.append(f"{p.name} 牌型：{self.result['hands'][p.id]}")
        for p in self.players:
            if awards[p.id]:
                self.events.append(f"{p.name} 赢得 ¥{awards[p.id]}")
        self.events.append("牌局结束")

    def view(self, viewer_id):
        reveal = self.phase == "complete" and bool(self.result and self.result["bestCards"])
        return {"phase": self.phase, "board": [card_text(c) for c in self.board],
                "dealer": self.players[self.dealer].id if self.dealer >= 0 and self.players else None,
                "turn": self.turn, "handNo": self.hand_no,
                "pot": sum(p.total_bet for p in self.players), "currentBet": self.current_bet,
                "players": [{"id": p.id, "name": p.name, "stack": p.stack, "buyin": p.buyin,
                             "pendingBuyin": p.pending_buyin,
                             "bot": p.bot, "connected": p.connected, "inHand": p.in_hand,
                             "folded": p.folded, "allIn": p.all_in, "streetBet": p.street_bet,
                             "totalBet": p.total_bet, "lastAction": p.last_action,
                             "actionSeq": p.action_seq,
                             "cards": [card_text(c) for c in p.cards] if (p.id == viewer_id or reveal and p.in_hand and not p.folded) else ["??"] * len(p.cards)}
                            for p in self.players if not p.departed],
                "options": self.options(next((p for p in self.players if p.id == viewer_id), None)) if any(p.id == viewer_id for p in self.players) else None,
                "result": self.result, "events": self.events}
