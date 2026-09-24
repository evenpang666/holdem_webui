from poker import Player, Table, best_five, score, score_five


def test_hand_ranking_and_wheel():
    wheel = [(14, "s"), (2, "s"), (3, "s"), (4, "s"), (5, "s")]
    assert score_five(wheel) == (8, 5)
    full_house = [(14, "s"), (14, "h"), (14, "d"), (13, "s"), (13, "h")]
    assert score_five(full_house) == (6, 14, 13)
    assert score(full_house + [(2, "c"), (3, "c")]) == (6, 14, 13)


def test_heads_up_button_blinds_and_streets():
    table = Table()
    table.add(Player("a", "A", 100, 100))
    table.add(Player("b", "B", 100, 100))
    table.start()
    assert table.players[table.dealer].id == "a"
    assert table.turn == "a"  # In heads-up, dealer is small blind and acts first preflop.
    assert [p.street_bet for p in table.players] == [5, 10]
    table.act("a", "call")
    table.act("b", "call")
    assert table.phase == "flop" and len(table.board) == 3 and table.turn == "b"
    for next_phase, count in [("turn", 4), ("river", 5), ("complete", 5)]:
        table.act("b", "call")
        table.act("a", "call")
        assert table.phase == next_phase and len(table.board) == count
    assert sum(p.stack for p in table.players) == 200


def test_short_all_in_does_not_reopen_betting():
    table = Table()
    table.add(Player("a", "A", 100, 100))
    table.add(Player("b", "B", 15, 15))
    table.add(Player("c", "C", 100, 100))
    table.start()  # A button, B small blind, C big blind.
    table.act("a", "call")
    table.act("b", "raise", 15)
    table.act("c", "call")
    assert table.turn == "a"
    assert table.options(table.players[0])["canRaise"] is False
    table.act("a", "call")
    assert table.phase == "flop"


def test_side_pots_and_whole_chips():
    table = Table()
    a = Player("a", "A", 0, 50)
    b = Player("b", "B", 0, 100)
    c = Player("c", "C", 0, 100)
    table.players = [a, b, c]
    table.dealer = 0
    table.phase = "river"
    table.board = [(14, "s"), (13, "s"), (12, "s"), (2, "h"), (3, "c")]
    a.cards = [(11, "s"), (10, "s")]  # Royal flush takes the main pot.
    b.cards = [(9, "h"), (9, "d")]
    c.cards = [(2, "c"), (2, "d")]  # Three of a kind takes the side pot.
    for p, bet in [(a, 50), (b, 100), (c, 100)]:
        p.in_hand = True
        p.total_bet = bet
    table.settle()
    assert table.result["awards"] == {"a": 150, "b": 0, "c": 100}
    assert sum(p.stack for p in table.players) == 250
    assert all(p.stack % 5 == 0 for p in table.players)


def test_hidden_cards_for_other_players():
    table = Table()
    table.add(Player("a", "A", 100, 100))
    table.add(Player("b", "B", 100, 100))
    table.start()
    state = table.view("a")
    assert state["players"][0]["cards"] != ["??", "??"]
    assert state["players"][1]["cards"] == ["??", "??"]


def test_fold_awards_pot_without_showdown():
    table = Table()
    table.add(Player("a", "A", 100, 100))
    table.add(Player("b", "B", 100, 100))
    table.start()
    table.act("a", "fold")
    assert table.phase == "complete"
    assert table.players[0].stack == 95
    assert table.players[1].stack == 105
    assert table.result["hands"] == {"b": "未摊牌"}
    assert sum(p.stack for p in table.players) == 200


def test_best_five_marks_exact_cards_including_wheel():
    cards = [(14, "s"), (2, "s"), (3, "s"), (4, "s"), (5, "s"), (13, "h"), (13, "d")]
    chosen = best_five(cards)
    assert set(chosen) == set(cards[:5])
    assert score_five(chosen) == score(cards)


def test_remove_current_player_folds_and_preserves_pot():
    table = Table()
    table.add(Player("a", "A", 100, 100))
    table.add(Player("b", "B", 100, 100))
    table.add(Player("c", "C", 100, 100))
    table.start()
    assert table.turn == "a"
    table.remove("a")
    assert table.turn == "b"
    assert all(p["id"] != "a" for p in table.view("b")["players"])
    table.act("b", "fold")
    assert table.phase == "complete"
    assert sum(p.stack for p in table.players) == 300
    table.prune_departed()
    assert len(table.players) == 2


def test_showdown_contains_each_contenders_best_five():
    table = Table()
    table.players = [Player("a", "A", 0, 50), Player("b", "B", 0, 50)]
    table.dealer = 0
    table.phase = "river"
    table.board = [(14, "s"), (13, "s"), (12, "s"), (2, "h"), (3, "c")]
    table.players[0].cards = [(11, "s"), (10, "s")]
    table.players[1].cards = [(2, "c"), (2, "d")]
    for p in table.players:
        p.in_hand = True
        p.total_bet = 50
    table.settle()
    assert table.result["hands"] == {"a": "同花顺", "b": "三条"}
    assert set(table.result["bestCards"]["a"]) == {"As", "Ks", "Qs", "Js", "Ts"}
    assert len(table.result["bestCards"]["b"]) == 5
