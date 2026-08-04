"""Pure daily rotation logic tests."""
import pytest

from ppbot.daily import (
    DailyMember,
    advance_next,
    apply_skip,
    apply_substitute,
    format_ru_date,
    member_list_text,
    next_leader,
    parse_vacation_date,
    parse_vacation_range,
    set_leader,
)


def member(
    pos, name="X", user_id=None, username=None, skip_date=None,
    vacation_until=None, vacation_start=None,
):
    return DailyMember(
        chat_id=1,
        position=pos,
        first_name=name,
        user_id=user_id,
        username=username,
        skip_date=skip_date,
        vacation_until=vacation_until,
        vacation_start=vacation_start,
    )


def names(members):
    return [m.first_name for m in members]


def test_leader_with_skipped_first():
    m = [
        member(0, "A", user_id=1, skip_date="2026-08-03"),
        member(1, "B", user_id=2),
        member(2, "C", user_id=3),
    ]
    leader = next_leader(m, 0, "2026-08-03")
    assert leader is not None
    assert leader.first_name == "B"


def test_all_skipped_returns_none():
    m = [
        member(0, "A", user_id=1, skip_date="2026-08-03"),
        member(1, "B", user_id=2, skip_date="2026-08-03"),
    ]
    assert next_leader(m, 0, "2026-08-03") is None


def test_advance_next_wraps():
    m = [member(0, "A"), member(1, "B"), member(2, "C")]
    assert advance_next(m, 2) == 0


def test_substitute_swap_today_b_tomorrow_a():
    """User scenario: today A leads (next_index=0), A asks B to substitute.
    Result: today B leads, tomorrow A leads. Queue becomes [B, A, C]."""
    m = [
        member(0, "A", user_id=1),
        member(1, "B", user_id=2),
        member(2, "C", user_id=3),
    ]
    new_members, new_next, msg = apply_substitute(m, 0, 0, "2026-08-03")
    assert names(new_members) == ["B", "A", "C"]
    assert new_next == 0  # next_index does not move on substitute
    assert msg == "Сегодня ведёт B, завтра A"

    # today B leads
    assert new_members[0].first_name == "B"
    # tomorrow: advance from A's slot (position 1) -> index 2 -> wraps... 
    # Wait: next_index stays 0, which now points at position 0 = B (today).
    # After today's reminder, scheduler advances next_index past B -> 1 = A.
    # Simulate the scheduler's post-reminder advance:
    leader_today = new_members[0]
    assert leader_today.first_name == "B"
    next_for_tomorrow = advance_next(new_members, leader_today.position)
    assert next_for_tomorrow == 1
    assert new_members[next_for_tomorrow].first_name == "A"


def test_substitute_with_stale_next_index():
    """Robustness: apply_substitute uses the leader POSITION from the button
    payload, so a stale next_index (1, pointing past A) does not misroute the
    swap: A<->B happen, next_index stays 1 (now A's slot) -> tomorrow A leads."""
    m = [
        member(0, "A", user_id=1),
        member(1, "B", user_id=2),
        member(2, "C", user_id=3),
    ]
    new_members, new_next, msg = apply_substitute(m, 1, 0, "2026-08-03")
    assert names(new_members) == ["B", "A", "C"]
    assert new_next == 1  # stays; position 1 is A -> tomorrow A leads
    assert msg == "Сегодня ведёт B, завтра A"
    leader_tomorrow = next_leader(new_members, new_next, "2026-08-04")
    assert leader_tomorrow.first_name == "A"


def test_substitute_wraps_to_first():
    m = [
        member(0, "A", user_id=1),
        member(1, "B", user_id=2),
        member(2, "C", user_id=3),
    ]
    # next_index=2 -> leader is C (position 2); next non-skipped after C is A (wraps)
    new_members, new_next, msg = apply_substitute(m, 2, 2, "2026-08-03")
    assert names(new_members) == ["C", "B", "A"]
    assert msg == "Сегодня ведёт A, завтра C"


def test_substitute_skips_skipped_members():
    m = [
        member(0, "A", user_id=1),
        member(1, "B", user_id=2, skip_date="2026-08-03"),
        member(2, "C", user_id=3),
    ]
    new_members, new_next, msg = apply_substitute(m, 0, 0, "2026-08-03")
    # next non-skipped after A(0) is C(2)
    assert names(new_members) == ["C", "B", "A"]
    assert msg == "Сегодня ведёт C, завтра A"


def test_substitute_single_member_error():
    m = [member(0, "A", user_id=1)]
    _, _, msg = apply_substitute(m, 0, 0, "2026-08-03")
    assert msg == "Некого подменять"


def test_skip_marks_and_repicks_leader():
    m = [
        member(0, "A", user_id=1),
        member(1, "B", user_id=2),
        member(2, "C", user_id=3),
    ]
    new_members, new_next, new_leader, err = apply_skip(m, 0, 0, "2026-08-03")
    assert err is None
    assert new_members[0].skip_date == "2026-08-03"
    assert new_leader.first_name == "B"
    assert new_next == 1  # points AT the new leader B (position 1)

    # B was never advanced; if B skips too, next is C
    m2, next2, leader2, err2 = apply_skip(m, 0, 1, "2026-08-03")
    assert err2 is None
    assert leader2.first_name == "C"
    assert next2 == 2  # points AT the new leader C (position 2)


def test_skip_all_members_returns_none_leader():
    m = [
        member(0, "A", user_id=1),
        member(1, "B", user_id=2),
    ]
    m, _, _, _ = apply_skip(m, 0, 0, "2026-08-03")
    m, _, leader, _ = apply_skip(m, 0, 1, "2026-08-03")
    assert leader is None


def test_skip_date_yesterday_is_active():
    m = [
        member(0, "A", user_id=1, skip_date="2026-08-02"),
        member(1, "B", user_id=2),
    ]
    leader = next_leader(m, 0, "2026-08-03")
    assert leader.first_name == "A"


def test_leader_none_on_empty():
    assert next_leader([], 0, "2026-08-03") is None


# ---- vacation (T1) ----

def test_is_on_vacation_inclusive_boundary():
    m = member(0, "A", vacation_until="2026-08-05")
    assert m.is_on_vacation("2026-08-05") is True
    assert m.is_on_vacation("2026-08-04") is True
    assert m.is_on_vacation("2026-08-06") is False


def test_is_on_vacation_false_when_unset():
    m = member(0, "A")
    assert m.is_on_vacation("2026-08-05") is False


def test_is_on_vacation_range_inside():
    m = member(0, "A", vacation_start="2026-08-05", vacation_until="2026-08-10")
    assert m.is_on_vacation("2026-08-07") is True


def test_is_on_vacation_range_inclusive_start_and_end():
    m = member(0, "A", vacation_start="2026-08-05", vacation_until="2026-08-10")
    assert m.is_on_vacation("2026-08-05") is True
    assert m.is_on_vacation("2026-08-10") is True


def test_is_on_vacation_range_outside():
    m = member(0, "A", vacation_start="2026-08-05", vacation_until="2026-08-10")
    assert m.is_on_vacation("2026-08-04") is False
    assert m.is_on_vacation("2026-08-11") is False


def test_is_on_vacation_legacy_with_start_none():
    m = member(0, "A", vacation_start=None, vacation_until="2026-08-05")
    assert m.is_on_vacation("2026-08-05") is True
    assert m.is_on_vacation("2026-08-06") is False


def test_is_unavailable_combines_skip_and_vacation():
    skipped = member(0, "A", skip_date="2026-08-03")
    assert skipped.is_unavailable("2026-08-03") is True
    assert skipped.is_unavailable("2026-08-04") is False
    vacationer = member(1, "B", vacation_until="2026-08-05")
    assert vacationer.is_unavailable("2026-08-03") is True
    assert vacationer.is_unavailable("2026-08-06") is False


def test_plain_name_returns_first_name_even_with_username():
    m = member(0, "Иван", username="ivanov")
    assert m.plain_name == "Иван"
    assert m.display_name == "@ivanov"


def test_parse_vacation_date_ru_format():
    assert parse_vacation_date("05.08.2026") == "2026-08-05"
    assert parse_vacation_date("5.8.2026") == "2026-08-05"
    assert parse_vacation_date("05/08/2026") == "2026-08-05"


def test_parse_vacation_date_iso_format():
    assert parse_vacation_date("2026-08-05") == "2026-08-05"


def test_parse_vacation_date_invalid():
    assert parse_vacation_date("32.13.2026") is None
    assert parse_vacation_date("2026-13-05") is None
    assert parse_vacation_date("garbage") is None
    assert parse_vacation_date("") is None
    assert parse_vacation_date("05.08.26") is None


def test_parse_vacation_range_ru_range():
    assert parse_vacation_range("05.08.2026-10.08.2026") == ("2026-08-05", "2026-08-10")
    assert parse_vacation_range("5.8.2026-10.8.2026") == ("2026-08-05", "2026-08-10")


def test_parse_vacation_range_single_date():
    assert parse_vacation_range("05.08.2026") == (None, "2026-08-05")
    assert parse_vacation_range("2026-08-05") == (None, "2026-08-05")


def test_parse_vacation_range_inverted_rejected():
    assert parse_vacation_range("10.08.2026-05.08.2026") is None


def test_parse_vacation_range_mixed_format_rejected():
    assert parse_vacation_range("05.08.2026-2026-08-10") is None


def test_parse_vacation_range_invalid():
    assert parse_vacation_range("garbage") is None
    assert parse_vacation_range("") is None


def test_format_ru_date():
    assert format_ru_date("2026-08-05") == "05.08.2026"


def test_member_list_text_vacation_suffix():
    m = [
        member(0, "Иван", vacation_until="2026-08-05"),
        member(1, "Пётр"),
    ]
    text = member_list_text(m)
    assert "1. Иван (в отпуске до 05.08.2026)" in text
    assert "2. Пётр" in text


def test_to_dict_from_dict_roundtrip_vacation():
    m = member(0, "Иван", vacation_until="2026-08-05")
    restored = DailyMember.from_dict(m.to_dict())
    assert restored.vacation_until == "2026-08-05"


def test_from_dict_old_dict_without_vacation():
    dct = member(0, "Иван").to_dict()
    dct.pop("vacation_until")
    restored = DailyMember.from_dict(dct)
    assert restored.vacation_until is None


# ---- vacation-aware rotation (T2) ----

def test_next_leader_skips_vacationer():
    m = [
        member(0, "A", user_id=1, vacation_until="2026-08-03"),
        member(1, "B", user_id=2),
        member(2, "C", user_id=3),
    ]
    leader = next_leader(m, 0, "2026-08-03")
    assert leader is not None
    assert leader.first_name == "B"


def test_all_on_vacation_returns_none():
    m = [
        member(0, "A", user_id=1, vacation_until="2026-08-05"),
        member(1, "B", user_id=2, vacation_until="2026-08-05"),
    ]
    assert next_leader(m, 0, "2026-08-03") is None


def test_vacationer_returns_before_today_leader_available():
    """Vacation that ended yesterday no longer blocks rotation."""
    m = [
        member(0, "A", user_id=1, vacation_until="2026-08-02"),
        member(1, "B", user_id=2),
    ]
    leader = next_leader(m, 0, "2026-08-03")
    assert leader.first_name == "A"


def test_substitute_skips_vacationer_as_b():
    m = [
        member(0, "A", user_id=1),
        member(1, "B", user_id=2, vacation_until="2026-08-03"),
        member(2, "C", user_id=3),
    ]
    new_members, _, msg = apply_substitute(m, 0, 0, "2026-08-03")
    # next available after A(0) is C(2)
    assert [x.first_name for x in new_members] == ["C", "B", "A"]
    assert msg == "Сегодня ведёт C, завтра A"


def test_skip_repick_skips_vacationer():
    m = [
        member(0, "A", user_id=1),
        member(1, "B", user_id=2, vacation_until="2026-08-03"),
        member(2, "C", user_id=3),
    ]
    _, new_next, new_leader, err = apply_skip(m, 0, 0, "2026-08-03")
    assert err is None
    assert new_leader.first_name == "C"
    assert new_next == 2


def test_skip_rejected_for_vacationer():
    m = [
        member(0, "A", user_id=1),
        member(1, "B", user_id=2, vacation_until="2026-08-03"),
    ]
    _, _, _, err = apply_skip(m, 0, 1, "2026-08-03")
    assert err == "Участник в отпуске, пропуск не нужен"


# ---- set_leader (T2) ----

def test_set_leader_points_next_index_and_keeps_order():
    m = [
        member(0, "A", user_id=1),
        member(1, "B", user_id=2),
        member(2, "C", user_id=3),
    ]
    new_next, err = set_leader(m, 0, 1, "2026-08-03")
    assert err is None
    assert new_next == 1
    assert [x.first_name for x in m] == ["A", "B", "C"]  # no reorder


def test_set_leader_tomorrow_continues_after_chosen():
    """After set_leader(B), the nightly advance resumes from the member after B."""
    m = [
        member(0, "A", user_id=1),
        member(1, "B", user_id=2),
        member(2, "C", user_id=3),
    ]
    new_next, _ = set_leader(m, 0, 1, "2026-08-03")
    assert new_next == 1
    # today B leads; at 23:59 advance past B -> C
    next_for_tomorrow = advance_next(m, new_next)
    assert next_for_tomorrow == 2
    assert m[next_for_tomorrow].first_name == "C"


def test_set_leader_rejects_vacationer():
    m = [
        member(0, "A", user_id=1),
        member(1, "B", user_id=2, vacation_until="2026-08-03"),
    ]
    new_next, err = set_leader(m, 0, 1, "2026-08-03")
    assert err == "Недоступен сегодня (отпуск или пропуск)"
    assert new_next == 0


def test_set_leader_rejects_skipped_member():
    m = [
        member(0, "A", user_id=1, skip_date="2026-08-03"),
        member(1, "B", user_id=2),
    ]
    new_next, err = set_leader(m, 0, 0, "2026-08-03")
    assert err == "Недоступен сегодня (отпуск или пропуск)"
    assert new_next == 0


def test_set_leader_unknown_position():
    m = [member(0, "A", user_id=1)]
    _, err = set_leader(m, 0, 5, "2026-08-03")
    assert err == "Участник не найден"


def test_set_leader_empty_team():
    _, err = set_leader([], 0, 0, "2026-08-03")
    assert err == "Команда пуста"
