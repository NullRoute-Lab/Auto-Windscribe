import asyncio
import json

import pytest
from textual.widgets import DataTable, Input

from auto_windscribe.cli import dispatch, main, parser
from auto_windscribe.demo import demo_services
from auto_windscribe.models import State
from auto_windscribe.tui import Confirm, Help, Preferences, WindscribeApp


def test_help_without_native_cli(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    assert "scan" in capsys.readouterr().out


@pytest.mark.parametrize(
    "args",
    [
        ["--offline", "status"],
        ["status", "--offline"],
        ["scan", "--profile", "restrictive", "--dry-run"],
    ],
)
def test_global_options_before_or_after_commands(args):
    parsed = parser().parse_args(args)
    assert getattr(parsed, "offline", False) or getattr(parsed, "dry_run", False)


@pytest.mark.parametrize(
    "args",
    [
        ["status", "--offline", "--json"],
        ["doctor", "--offline"],
        ["results", "--offline"],
        ["profiles", "--offline"],
        ["disconnect", "--dry-run"],
    ],
)
def test_safe_cli_commands_without_real_process(args):
    assert main(args) == 0


def test_live_cli_guard_cannot_be_bypassed_by_flag():
    assert main(["scan", "--offline", "--live"]) == 2
    assert main(["disconnect", "--offline", "--live"]) == 2


async def test_demo_scan_cli_and_results(paths, capsys):
    services = demo_services(paths)
    try:
        assert (
            await dispatch(
                parser().parse_args(["scan", "--profile", "default", "--json"]), services
            )
            == 0
        )
        result = json.loads(capsys.readouterr().out)
        assert result["state"] == "succeeded"
        assert await dispatch(parser().parse_args(["results", "--json"]), services) == 0
        assert "Example" in capsys.readouterr().out
    finally:
        services.store.close()


async def test_dry_run_does_not_create_scan_or_connect(paths):
    services = demo_services(paths)
    try:
        await dispatch(parser().parse_args(["scan", "--dry-run", "--json"]), services)
        assert services.vpn.calls == []
        assert services.store.scans() == []
    finally:
        services.store.close()


async def test_tui_search_filters_preferences_help_and_quit(paths):
    services = demo_services(paths)
    app = WindscribeApp(services)
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            await app.workers.wait_for_complete()
            table = app.query_one("#servers", DataTable)
            assert table.row_count == 10
            await pilot.press("slash")
            assert isinstance(app.focused, Input)
            app.query_one("#search", Input).value = "North"
            await pilot.pause()
            assert table.row_count == 5
            await pilot.press("escape", "space")
            assert len(app.marked) == 1
            await pilot.press("f")
            assert isinstance(app.screen, Preferences)
            await pilot.press("escape")
            await pilot.press("p")
            assert isinstance(app.screen, Preferences)
            await pilot.press("escape")
            await pilot.press("question_mark")
            assert isinstance(app.screen, Help)
            await pilot.press("escape")
            await pilot.press("q")
        assert services.vpn.calls == []
    finally:
        services.store.close()


async def test_tui_confirm_connect_then_quit_retains_tunnel(paths):
    services = demo_services(paths)
    app = WindscribeApp(services)
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.press("enter")
            assert isinstance(app.screen, Confirm)
            await pilot.click("#confirm")
            await app.workers.wait_for_complete()
            assert services.vpn.current.state == State.CONNECTED
            await pilot.press("q")
        assert services.vpn.calls == ["connect"]
    finally:
        services.store.close()


async def test_tui_scan_responsive_stop_retains_connection(paths):
    services = demo_services(paths)
    from tests.fakes import FakeProbe

    services.controller.probe = FakeProbe(delay=10)
    app = WindscribeApp(services)
    try:
        async with app.run_test(size=(100, 32)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.press("r")
            assert isinstance(app.screen, Confirm)
            await pilot.click("#confirm")
            await asyncio.wait_for(services.controller.probe.started.wait(), 2)
            await pilot.press("slash")
            assert isinstance(app.focused, Input)
            await pilot.press("escape", "x")
            await app.workers.wait_for_complete()
            assert services.vpn.current.state == State.CONNECTED
            assert services.store.scans()[0]["state"] == "paused"
            await pilot.press("q")
        assert "disconnect" not in services.vpn.calls
    finally:
        services.store.close()


async def test_quit_during_scan_cancels_without_disconnect(paths):
    from tests.fakes import FakeProbe

    services = demo_services(paths)
    services.controller.probe = FakeProbe(delay=10)
    app = WindscribeApp(services)
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            await app.workers.wait_for_complete()
            app.start_scan()
            await asyncio.wait_for(services.controller.probe.started.wait(), 2)
            await pilot.press("q")
        assert "disconnect" not in services.vpn.calls
        assert services.store.scans()[0]["state"] == "paused"
    finally:
        services.store.close()


async def test_profile_edit_persists_individual_protocol_ports(paths):
    services = demo_services(paths)
    app = WindscribeApp(services)
    try:
        async with app.run_test(size=(120, 40)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.press("s")
            app.screen.query_one("#pref-name", Input).value = "custom"
            app.screen.query_one("#pref-ports-stealth", Input).value = "443"
            await pilot.click("#apply")
            await pilot.pause()
            assert services.profiles.get("custom").ports == {"stealth": [443]}
            await pilot.press("slash", "q")
            assert app.query_one("#search", Input).value == "q"
            await pilot.press("escape", "q")
    finally:
        services.store.close()


async def test_cli_all_read_commands_with_fake_adapters(paths, capsys):
    services = demo_services(paths)
    try:
        for args in (
            ["locations", "--json"],
            ["locations", "--countries"],
            ["locations", "--cities"],
            ["protocols", "--json"],
            ["profiles", "show", "restrictive", "--json"],
            ["results", "--history"],
            ["results", "--scans"],
        ):
            assert await dispatch(parser().parse_args(args), services) == 0
            assert capsys.readouterr().out
    finally:
        services.store.close()


async def test_cli_connect_disconnect_and_profile_save_fake_only(paths, capsys):
    services = demo_services(paths)
    try:
        args = parser().parse_args(
            ["connect", "Example One", "--protocol", "stealth", "--port", "443", "--json"]
        )
        assert await dispatch(args, services) == 0
        assert json.loads(capsys.readouterr().out)["outcome"] == "success"
        assert await dispatch(parser().parse_args(["disconnect", "--json"]), services) == 0
        assert services.vpn.calls == ["connect", "disconnect"]
        file = paths.config / "import.json"
        file.write_text('{"protocols":["stealth"],"max_tests":4}')
        await dispatch(
            parser().parse_args(["profiles", "save", "imported", "--file", str(file)]), services
        )
        assert services.profiles.get("imported").max_tests == 4
    finally:
        services.store.close()


async def test_slow_status_refresh_is_not_cancelled_by_next_poll(paths):
    from unittest.mock import AsyncMock

    services = demo_services(paths)
    app = WindscribeApp(services)
    try:
        async with app.run_test(size=(100, 32)):
            await app.workers.wait_for_complete()

            async def slow_status():
                await asyncio.sleep(0.05)
                return services.vpn.current

            services.vpn.status = AsyncMock(side_effect=slow_status)
            first = app.refresh_status()
            app.refresh_status()
            await app.workers.wait_for_complete()
            assert not first.is_cancelled
            assert services.vpn.status.await_count == 1
            app.request_exit()
    finally:
        services.store.close()
