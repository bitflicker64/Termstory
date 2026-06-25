"""Lightweight test helpers for TermStory's Textual-based TUI.

Provides a monkeypatch-based workaround for Textual 8.x AwaitComplete
pre_await callback issue (tracked in termstory#165, upstream bug filed).

The problem: ModalScreen.dismiss() + pop_screen() return AwaitComplete
objects with a pre_await callback that raises ScreenError if awaited from
inside the screen's own message-pump context.  Under run_test(),
pilot.pause() processes call_next callbacks, which triggers the
AwaitComplete and hangs.

Usage:

    from termstory.testing import install_sync_dismiss_workaround

    @pytest.mark.asyncio
    async def test_dismiss_chain(monkeypatch):
        install_sync_dismiss_workaround(monkeypatch)
        async with app.run_test() as pilot:
            ...
            await pilot.pause()  # doesn't hang

The workaround replaces ModalScreen.dismiss so that it:
1. Fires the result callback synchronously (e.g. handle_onboarding_result)
2. Pops the screen stack directly without AwaitComplete
3. Returns None instead of AwaitComplete
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from _pytest.monkeypatch import MonkeyPatch


def install_sync_dismiss_workaround(monkeypatch: MonkeyPatch) -> None:
    """Replace ModalScreen.dismiss with a synchronous version safe for
    run_test().

    In production, ModalScreen.dismiss() calls the result callback,
    then calls app.pop_screen() which returns an AwaitComplete.  The
    AwaitComplete's pre_await callback raises ScreenError if the
    test pump awaits it from inside the screen's message-handler
    context, blocking pilot.pause().

    This replacement fires the callback directly, pops the screen
    stack manually, and returns ``None``.  The underlying AwaitComplete
    machinery is never created, so the test pump has nothing to block on.

    The workaround does NOT replace ``pop_screen`` on the App itself,
    so non-ModalScreen dismiss paths are unaffected.  It is also
    transparent to the *result callback* — the same dictionary is
    delivered to handle_onboarding_result as in production.

    .. warning::
        Only use inside test modules where the AwaitComplete-induced hang
        is known to occur.  Do not patch production imports.
    """
    from textual.screen import ModalScreen

    original_dismiss = ModalScreen.dismiss  # noqa: F841 — keep for reset if needed

    def _sync_dismiss(self, result: object = None) -> None:
        """Replace dismiss with sync callback + direct stack pop."""
        # 1. Fire the result callback — this is what production dismiss does
        #    synchronously before constructing the AwaitComplete.
        if self._result_callbacks:
            callback = self._result_callbacks[-1]
            callback(result)

        # 2. Pop the screen from the stack directly.  In production this is
        #    done via app.pop_screen() which returns an AwaitComplete wrapping
        #    do_pop() -> _replace_screen.  In run_test() that AwaitComplete
        #    blocks the pump; we bypass it by manipulating the stack.
        stack = self.app._screen_stack
        if len(stack) > 1:
            popped = stack.pop()
            popped._pop_result_callback()

        # 3. Return None — no AwaitComplete for the pump to await.
        return None

    monkeypatch.setattr(ModalScreen, "dismiss", _sync_dismiss)
