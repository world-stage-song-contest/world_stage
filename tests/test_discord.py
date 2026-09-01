import json

from flask import Flask
from hypothesis import given
from hypothesis import strategies as st

import world_stage.discord as discord

FLAGS = {
    "ES": "🇪🇸",
    "FR": "🇫🇷",
    "PL": "🇵🇱",
    "US": "🇺🇸",
}


class _Response:
    status = 204

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None


def test_qualification_embed_groups_entries_and_uses_responsive_columns(monkeypatch):
    requests = []
    app = Flask(__name__)
    app.config.update(
        DISCORD_WEBHOOK_URL="https://discord.example/webhook",
        DISCORD_WEBHOOK_TIMEOUT=1,
    )

    @given(
        direct=st.lists(
            st.sampled_from([("PL", "Alpha"), ("ES", "Beta"), ("FR", "Gamma")]),
            min_size=1,
            max_size=3,
            unique=True,
        ),
        repe=st.lists(
            st.sampled_from([("US", "Delta"), ("ES", "Epsilon"), ("FR", "Zeta")]),
            min_size=1,
            max_size=3,
            unique=True,
        ),
        from_repechage=st.booleans(),
    )
    def property_test(direct, repe, from_repechage):
        groups = [(1, "f", "Final", direct)]
        if not from_repechage:
            groups.append((2, "sc", "Repechage", repe))
        rows = [
            {
                "show_name": "Repechage" if from_repechage else "Semi-Final 1",
                "source_show_type": "sc" if from_repechage else "sf",
                "year_id": 2026,
                "special_name": None,
                "target_show_id": target_id,
                "target_show_name": target_name,
                "target_show_type": target_type,
                "qualifier_order": order,
                "country_id": country_id,
                "title": title,
            }
            for target_id, target_type, target_name, entries in groups
            for order, (country_id, title) in enumerate(entries, 1)
        ]

        class Cursor:
            def execute(self, *_args):
                pass

            def fetchall(self):
                return rows

        class Database:
            def cursor(self):
                return Cursor()

        monkeypatch.setattr(discord, "get_db", Database)
        monkeypatch.setattr(
            discord.urllib.request,
            "urlopen",
            lambda request, timeout: requests.append(request) or _Response(),
        )

        with app.app_context():
            discord.send_qualification_notification(1)

        payload = json.loads(requests[-1].data)
        fields = payload["embeds"][0]["fields"]
        assert [field["name"] for field in fields] == [
            "Final",
            *([] if from_repechage else ["Repe"]),
        ]
        assert all(field["inline"] == (not from_repechage) for field in fields)
        expected_entries = [direct] if from_repechage else [direct, repe]
        reveal_number = 1
        expected_lines = []
        for entries in expected_entries:
            group_lines = []
            for country_id, title in entries:
                group_lines.append(
                    f"`{reveal_number:02d}` {FLAGS[country_id]} {title}"
                )
                reveal_number += 1
            expected_lines.append(group_lines)
        assert [field["value"].splitlines() for field in fields] == expected_lines

    property_test()


def test_running_order_embed_splits_after_the_larger_half(monkeypatch):
    requests = []
    app = Flask(__name__)
    app.config.update(
        DISCORD_WEBHOOK_URL="https://discord.example/webhook",
        DISCORD_WEBHOOK_TIMEOUT=1,
    )

    @given(total=st.integers(min_value=1, max_value=25))
    def property_test(total):
        country_codes = list(FLAGS)
        rows = [
            {
                "show_name": "Semi-Final 1",
                "year_id": 2026,
                "special_name": None,
                "running_order": position,
                "country_id": country_codes[(position - 1) % len(country_codes)],
                "title": f"Song {position}",
            }
            for position in range(1, total + 1)
        ]

        class Cursor:
            def execute(self, *_args):
                pass

            def fetchall(self):
                return rows

        class Database:
            def cursor(self):
                return Cursor()

        monkeypatch.setattr(discord, "get_db", Database)
        monkeypatch.setattr(
            discord.urllib.request,
            "urlopen",
            lambda request, timeout: requests.append(request) or _Response(),
        )

        with app.app_context():
            discord.send_running_order_notification(1)

        payload = json.loads(requests[-1].data)
        fields = payload["embeds"][0]["fields"]
        midpoint = (total + 1) // 2
        expected_names = [f"1-{midpoint}"]
        if midpoint < total:
            expected_names.append(f"{midpoint + 1}-{total}")
        assert [field["name"] for field in fields] == expected_names
        assert all(field["inline"] is True for field in fields)
        assert [len(field["value"].splitlines()) for field in fields] == [
            midpoint,
            *([] if midpoint == total else [total - midpoint]),
        ]
        assert [line for field in fields for line in field["value"].splitlines()] == [
            f"`{position:02d}` {FLAGS[row['country_id']]} Song {position}"
            for position, row in enumerate(rows, 1)
        ]

    property_test()


def test_final_results_embed_adds_points_and_splits_after_the_larger_half(monkeypatch):
    requests = []
    app = Flask(__name__)
    app.config.update(
        DISCORD_WEBHOOK_URL="https://discord.example/webhook",
        DISCORD_WEBHOOK_TIMEOUT=1,
    )

    @given(
        points=st.lists(
            st.integers(min_value=0, max_value=10_000),
            min_size=1,
            max_size=25,
        )
    )
    def property_test(points):
        country_codes = list(FLAGS)
        rows = [
            {
                "show_name": "Final",
                "year_id": 2026,
                "special_name": None,
                "place": place,
                "total_points": total_points,
                "country_id": country_codes[(place - 1) % len(country_codes)],
                "title": f"Song {place}",
            }
            for place, total_points in enumerate(points, 1)
        ]

        class Cursor:
            def execute(self, *_args):
                pass

            def fetchall(self):
                return rows

        class Database:
            def cursor(self):
                return Cursor()

        monkeypatch.setattr(discord, "get_db", Database)
        monkeypatch.setattr(
            discord.urllib.request,
            "urlopen",
            lambda request, timeout: requests.append(request) or _Response(),
        )

        with app.app_context():
            discord.send_final_results_notification(1)

        payload = json.loads(requests[-1].data)
        fields = payload["embeds"][0]["fields"]
        total = len(points)
        midpoint = (total + 1) // 2
        assert [field["name"] for field in fields] == [
            f"1-{midpoint}",
            *([] if midpoint == total else [f"{midpoint + 1}-{total}"]),
        ]
        assert all(field["inline"] is True for field in fields)
        assert [line for field in fields for line in field["value"].splitlines()] == [
            f"`{place:02d}` {FLAGS[row['country_id']]} Song {place} **{total_points}**"
            for place, (row, total_points) in enumerate(zip(rows, points, strict=True), 1)
        ]

    property_test()


def test_song_titles_are_clipped_only_at_word_boundaries():
    @given(
        words=st.lists(
            st.text(
                alphabet="abcdefghijklmnopqrstuvwxyz",
                min_size=1,
                max_size=35,
            ),
            min_size=1,
            max_size=8,
        )
    )
    def property_test(words):
        title = " ".join(words)
        clipped = discord._clip_title(title)

        if len(title) <= 25:
            assert clipped == title
            return
        if len(words) == 1:
            assert clipped == words[0]
            return
        eligible = [word for word in words if len(word) <= 25]
        if not eligible:
            assert clipped == ""
            return
        assert len(clipped) <= 25
        cleaned = " ".join(eligible)
        if len(cleaned) <= 25:
            assert clipped == cleaned
            return
        content = clipped.removesuffix("…")
        assert content in [
            " ".join(eligible[:end]) for end in range(1, len(eligible) + 1)
        ]
        assert clipped.endswith("…") or len(content) == 25

    property_test()


def test_special_country_flags_use_subdivision_and_custom_emoji():
    app = Flask(__name__)
    app.config.update(
        DISCORD_FLAG_YU_EMOJI_ID="123456789",
        DISCORD_FLAG_DD_EMOJI_ID="987654321",
    )
    expected = {
        "EN": discord.SUBDIVISION_FLAGS["EN"],
        "AB": discord.SUBDIVISION_FLAGS["AB"],
        "WA": discord.SUBDIVISION_FLAGS["WA"],
        "YU": "<:flag_yu:123456789>",
        "DD": "<:flag_dd:987654321>",
    }

    @given(country_id=st.sampled_from(list(expected)))
    def property_test(country_id):
        with app.app_context():
            assert discord._flag_emoji(country_id) == expected[country_id]

    property_test()
