"""Pure daily rotation logic tests."""
import datetime

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
        username=username or name,
        user_id=user_id,
        skip_date=skip_date,
        vacation_until=vacation_until,
        vacation_start=vacation_start,
    )


def names(members):
    return [m.username for m in members]


def test_leader_with_skipped_first():
    m = [
        member(0, "A", user_id=1, skip_date="2026-08-03"),
        member(1, "B", user_id=2),
        member(2, "C", user_id=3),
    ]
    leader = next_leader(m, 0, "2026-08-03")
    assert leader is not None
    assert leader.username == "B"


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
    """User scenario: today A leads (schedule[today]=0), B leads tomorrow
    (schedule[tomorrow]=1), A asks B to substitute. Result: today B leads,
    tomorrow A leads. Members list order NEVER changes — only schedule rows."""
    m = [
        member(0, "A", user_id=1),
        member(1, "B", user_id=2),
        member(2, "C", user_id=3),
    ]
    schedule = {"2026-08-03": 0, "2026-08-04": 1, "2026-08-05": 2}
    b_pos, a_pos, msg, err = apply_substitute(schedule, m, "2026-08-03", "2026-08-04")
    assert err is None
    assert b_pos == 1  # tomorrow's scheduled leader leads today
    assert a_pos == 0  # today's leader leads tomorrow
    assert msg == "Сегодня ведёт B, в следующий рабочий день A"
    # guardrail: member list order and positions untouched
    assert names(m) == ["A", "B", "C"]
    assert [x.position for x in m] == [0, 1, 2]


def test_substitute_with_stale_next_index():
    """The pure function has NO next_index parameter — a stale next_index in
    the chat row cannot misroute the swap, which reads A and B solely from
    the schedule dates."""
    m = [
        member(0, "A", user_id=1),
        member(1, "B", user_id=2),
        member(2, "C", user_id=3),
    ]
    schedule = {"2026-08-03": 0, "2026-08-04": 1, "2026-08-05": 2}
    b_pos, a_pos, msg, err = apply_substitute(schedule, m, "2026-08-03", "2026-08-04")
    assert err is None
    assert (b_pos, a_pos) == (1, 0)
    assert msg == "Сегодня ведёт B, в следующий рабочий день A"


def test_substitute_wraps_to_first():
    """Wrap-around: today's leader is C (position 2), tomorrow's scheduled
    leader is A (position 0) -> after the swap today A leads, tomorrow C."""
    m = [
        member(0, "A", user_id=1),
        member(1, "B", user_id=2),
        member(2, "C", user_id=3),
    ]
    schedule = {"2026-08-03": 2, "2026-08-04": 0, "2026-08-05": 1}
    b_pos, a_pos, msg, err = apply_substitute(schedule, m, "2026-08-03", "2026-08-04")
    assert err is None
    assert b_pos == 0
    assert a_pos == 2
    assert msg == "Сегодня ведёт A, в следующий рабочий день C"
    assert names(m) == ["A", "B", "C"]
    assert [x.position for x in m] == [0, 1, 2]


def test_substitute_skips_skipped_members():
    """B (tomorrow's scheduled leader) has skip_date == today -> cannot
    substitute, error. Members list untouched."""
    m = [
        member(0, "A", user_id=1),
        member(1, "B", user_id=2, skip_date="2026-08-03"),
        member(2, "C", user_id=3),
    ]
    schedule = {"2026-08-03": 0, "2026-08-04": 1, "2026-08-05": 2}
    b_pos, a_pos, msg, err = apply_substitute(schedule, m, "2026-08-03", "2026-08-04")
    assert err == "Некого подменять"
    assert (b_pos, a_pos, msg) == (None, None, None)
    assert names(m) == ["A", "B", "C"]
    assert [x.position for x in m] == [0, 1, 2]


def test_substitute_single_member_error():
    """Single-member team: schedule[today] == schedule[tomorrow] (both 0)
    -> a_pos == b_pos -> error."""
    m = [member(0, "A", user_id=1)]
    schedule = {"2026-08-03": 0, "2026-08-04": 0}
    b_pos, a_pos, msg, err = apply_substitute(schedule, m, "2026-08-03", "2026-08-04")
    assert err == "Некого подменять"
    assert (b_pos, a_pos, msg) == (None, None, None)


def test_substitute_tomorrow_row_missing_error():
    m = [member(0, "A", user_id=1), member(1, "B", user_id=2)]
    schedule = {"2026-08-03": 0}  # no tomorrow row in the window
    b_pos, a_pos, msg, err = apply_substitute(schedule, m, "2026-08-03", "2026-08-04")
    assert err == "Некого подменять"
    assert (b_pos, a_pos, msg) == (None, None, None)


def test_substitute_today_row_none_error():
    m = [member(0, "A", user_id=1), member(1, "B", user_id=2)]
    schedule = {"2026-08-03": None, "2026-08-04": 1}
    b_pos, a_pos, msg, err = apply_substitute(schedule, m, "2026-08-03", "2026-08-04")
    assert err == "Все пропущены сегодня, некого подменять"
    assert (b_pos, a_pos, msg) == (None, None, None)


def test_substitute_empty_team_error():
    b_pos, a_pos, msg, err = apply_substitute({}, [], "2026-08-03", "2026-08-04")
    assert err == "Команда пуста"
    assert (b_pos, a_pos, msg) == (None, None, None)


def test_substitute_guardrail_members_never_reordered():
    """Guardrail: after a successful substitute the members list is
    untouched — same object identity, same order, same positions."""
    m = [
        member(0, "A", user_id=1),
        member(1, "B", user_id=2),
        member(2, "C", user_id=3),
    ]
    before = [x for x in m]
    schedule = {"2026-08-03": 0, "2026-08-04": 1, "2026-08-05": 2}
    b_pos, a_pos, msg, err = apply_substitute(schedule, m, "2026-08-03", "2026-08-04")
    assert err is None
    assert [x.username for x in m] == ["A", "B", "C"]
    assert [x.position for x in m] == [0, 1, 2]
    assert all(a is b for a, b in zip(m, before))


def test_substitute_message_uses_display_name():
    m = [
        member(0, "Алиса", user_id=1, username="@alice"),
        member(1, "Боб", user_id=2, username="@bob"),
    ]
    schedule = {"2026-08-03": 0, "2026-08-04": 1}
    b_pos, a_pos, msg, err = apply_substitute(schedule, m, "2026-08-03", "2026-08-04")
    assert err is None
    assert (b_pos, a_pos) == (1, 0)
    assert msg == "Сегодня ведёт @bob, в следующий рабочий день @alice"


def test_skip_marks_and_repicks_leader():
    m = [
        member(0, "A", user_id=1),
        member(1, "B", user_id=2),
        member(2, "C", user_id=3),
    ]
    new_members, new_next, new_leader, err = apply_skip(m, 0, 0, "2026-08-03")
    assert err is None
    assert new_members[0].skip_date == "2026-08-03"
    assert new_leader.username == "B"
    assert new_next == 1  # points AT the new leader B (position 1)

    # B was never advanced; if B skips too, next is C
    m2, next2, leader2, err2 = apply_skip(m, 0, 1, "2026-08-03")
    assert err2 is None
    assert leader2.username == "C"
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
    assert leader.username == "A"


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


def test_plain_name_strips_at_from_creation_text():
    m = member(0, "Иван @ivanов")
    assert m.plain_name == "Иван"
    assert m.display_name == "Иван @ivanов"


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
    text = member_list_text(m, today="2026-08-03")
    assert "1. Иван (в отпуске до 05.08.2026)" in text
    assert "2. Пётр" in text


def test_member_list_text_no_badge_when_vacation_over():
    m = [member(0, "Иван", vacation_until="2026-08-02")]
    text = member_list_text(m, today="2026-08-03")
    assert "в отпуске" not in text


def test_member_list_text_no_badge_future_vacation():
    m = [member(0, "Иван", vacation_start="2026-08-05", vacation_until="2026-08-10")]
    text = member_list_text(m, today="2026-08-03")
    assert "в отпуске" not in text


def test_member_list_text_badge_inside_range():
    m = [member(0, "Иван", vacation_start="2026-08-05", vacation_until="2026-08-10")]
    text = member_list_text(m, today="2026-08-07")
    assert "1. Иван (в отпуске до 10.08.2026)" in text


def test_member_list_text_legacy_badge_until():
    m = [member(0, "Иван", vacation_until="2026-08-05")]
    assert "в отпуске" in member_list_text(m, today="2026-08-05")
    assert "в отпуске" not in member_list_text(m, today="2026-08-06")


def test_to_dict_from_dict_roundtrip_vacation():
    m = member(0, "Иван", vacation_until="2026-08-05")
    restored = DailyMember.from_dict(m.to_dict())
    assert restored.vacation_until == "2026-08-05"


def test_to_dict_from_dict_roundtrip_vacation_range():
    m = member(0, "Иван", vacation_start="2026-08-05", vacation_until="2026-08-10")
    restored = DailyMember.from_dict(m.to_dict())
    assert restored.vacation_start == "2026-08-05"
    assert restored.vacation_until == "2026-08-10"


def test_from_dict_old_dict_without_vacation_start():
    dct = member(0, "Иван", vacation_until="2026-08-05").to_dict()
    dct.pop("vacation_start")
    restored = DailyMember.from_dict(dct)
    assert restored.vacation_start is None
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
    assert leader.username == "B"


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
    assert leader.username == "A"


def test_substitute_skips_vacationer_as_b():
    """B (tomorrow's scheduled leader) is on vacation covering today ->
    cannot substitute, error. Members list untouched."""
    m = [
        member(0, "A", user_id=1),
        member(1, "B", user_id=2, vacation_until="2026-08-03"),
        member(2, "C", user_id=3),
    ]
    schedule = {"2026-08-03": 0, "2026-08-04": 1, "2026-08-05": 2}
    b_pos, a_pos, msg, err = apply_substitute(schedule, m, "2026-08-03", "2026-08-04")
    assert err == "Некого подменять"
    assert (b_pos, a_pos, msg) == (None, None, None)
    assert names(m) == ["A", "B", "C"]
    assert [x.position for x in m] == [0, 1, 2]


def test_skip_repick_skips_vacationer():
    m = [
        member(0, "A", user_id=1),
        member(1, "B", user_id=2, vacation_until="2026-08-03"),
        member(2, "C", user_id=3),
    ]
    _, new_next, new_leader, err = apply_skip(m, 0, 0, "2026-08-03")
    assert err is None
    assert new_leader.username == "C"
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
    assert [x.username for x in m] == ["A", "B", "C"]  # no reorder


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
    assert m[next_for_tomorrow].username == "C"


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


# ---- workday-aware build_schedule (T1 weekend-rotation-skip) ----

async def wd(d):
    return d.weekday() < 5


async def always_workday(d):
    return True


async def test_build_schedule_skips_non_workdays():
    from ppbot.daily import build_schedule

    m = [
        member(0, "U0", user_id=1),
        member(1, "U1", user_id=2),
        member(2, "U2", user_id=3),
    ]
    rows = await build_schedule(m, datetime.date(2026, 8, 8), 2, 5, workdays=wd)
    assert rows == [
        (datetime.date(2026, 8, 8), None),
        (datetime.date(2026, 8, 9), None),
        (datetime.date(2026, 8, 10), 2),
        (datetime.date(2026, 8, 11), 0),
        (datetime.date(2026, 8, 12), 1),
    ]


async def test_build_schedule_default_falls_back_mon_fri():
    from ppbot.daily import build_schedule

    m = [
        member(0, "U0", user_id=1),
        member(1, "U1", user_id=2),
        member(2, "U2", user_id=3),
    ]
    rows = await build_schedule(m, datetime.date(2026, 8, 8), 2, 5)
    assert rows == [
        (datetime.date(2026, 8, 8), None),
        (datetime.date(2026, 8, 9), None),
        (datetime.date(2026, 8, 10), 2),
        (datetime.date(2026, 8, 11), 0),
        (datetime.date(2026, 8, 12), 1),
    ]


async def test_build_schedule_working_saturday_when_calendar_says_workday():
    from ppbot.daily import build_schedule

    m = [
        member(0, "U0", user_id=1),
        member(1, "U1", user_id=2),
        member(2, "U2", user_id=3),
    ]
    rows = await build_schedule(m, datetime.date(2026, 8, 8), 2, 5, workdays=always_workday)
    assert rows == [
        (datetime.date(2026, 8, 8), 2),
        (datetime.date(2026, 8, 9), 0),
        (datetime.date(2026, 8, 10), 1),
        (datetime.date(2026, 8, 11), 2),
        (datetime.date(2026, 8, 12), 0),
    ]


def test_next_scheduled_date_picks_next_non_none():
    from ppbot.daily import next_scheduled_date

    schedule = {"2026-08-07": 0, "2026-08-08": None, "2026-08-09": None, "2026-08-10": 1}
    assert next_scheduled_date(schedule, "2026-08-07") == "2026-08-10"


def test_next_scheduled_date_skips_none_rows():
    from ppbot.daily import next_scheduled_date

    schedule = {"2026-08-06": 2, "2026-08-08": None, "2026-08-09": None, "2026-08-11": 0}
    assert next_scheduled_date(schedule, "2026-08-06") == "2026-08-11"
    assert next_scheduled_date(schedule, "2026-08-08") == "2026-08-11"


def test_next_scheduled_date_returns_none_when_absent():
    from ppbot.daily import next_scheduled_date

    assert next_scheduled_date({"2026-08-08": None}, "2026-08-06") is None
    assert next_scheduled_date({"2026-08-05": 0}, "2026-08-06") is None
    assert next_scheduled_date({}, "2026-08-06") is None


def test_get_display_name_vacation_strips_at():
    m = member(0, "Иван @ivanов", vacation_until="2026-08-05")
    assert m.get_display_name("2026-08-03") == "Иван"

def test_get_display_name_vacation_over_keeps_at():
    m = member(0, "Иван @ivanов", vacation_until="2026-08-02")
    assert m.get_display_name("2026-08-03") == "Иван @ivanов"

def test_get_display_name_no_vacation_keeps_at():
    m = member(0, "Иван @ivanов")
    assert m.get_display_name("2026-08-03") == "Иван @ivanов"

def test_get_display_name_no_username_returns_first_name():
    m = member(0, "Иван")
    assert m.get_display_name("2026-08-03") == "Иван"

def test_get_display_name_none_today_returns_display_name():
    m = member(0, "Иван @ivanов")
    assert m.get_display_name(None) == "Иван @ivanов"

def test_get_mention_vacation_strips_at():
    m = member(0, "Иван @ivanов", user_id=1, vacation_until="2026-08-05")
    assert m.get_mention("2026-08-03") == "Иван"

def test_member_list_text_vacation_strips_at():
    from ppbot.daily import member_list_text
    m = [
        member(0, "Иван @ivanов", vacation_until="2026-08-05"),
        member(1, "Пётр @petров"),
    ]
    text = member_list_text(m, today="2026-08-03")
    assert "1. Иван (в отпуске до 05.08.2026)" in text
    assert "2. Пётр @petров" in text


def test_display_name_returns_full_string():
    """display_name returns username as-is, including @username."""
    m = member(0, "Иван @ivanов")
    assert m.display_name == "Иван @ivanов"


def test_get_display_name_vacation_strips_at_from_string():
    """On vacation, get_display_name strips @username from name string."""
    m = member(0, "Иван @ivanов", vacation_until="2026-08-10")
    assert m.get_display_name("2026-08-05") == "Иван"


def test_get_display_name_not_vacation_keeps_full_string():
    """Not on vacation, get_display_name returns full name string."""
    m = member(0, "Иван @ivanов", vacation_until="2026-08-01")
    assert m.get_display_name("2026-08-05") == "Иван @ivanов"


def test_plain_name_strips_at():
    """plain_name strips @handle from name string."""
    m = member(0, "Иван @ivanов")
    assert m.plain_name == "Иван"


def test_plain_name_at_only_returns_username():
    """plain_name with only @handle returns @handle as-is."""
    m = member(0, "@ivanов")
    assert m.plain_name == "@ivanов"


def test_strip_at_handle_no_match():
    """_strip_at_handle returns text unchanged when no @handle to strip."""
    m = member(0, "Иван")
    assert m._strip_at_handle("Иван") == "Иван"
