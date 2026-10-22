// @vitest-environment jsdom
import { act, cleanup, renderHook } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { useFirstRunSetup } from './useFirstRunSetup';

afterEach(() => {
    cleanup();
    vi.useRealTimers();
    delete window.pywebview;
});

it('retries failed startup responses until setup status is available', async () => {
    vi.useFakeTimers();
    const getStatus = vi.fn()
        .mockResolvedValueOnce({ success: false, error: 'Brain starting' })
        .mockRejectedValueOnce(new Error('not ready'))
        .mockResolvedValue({ needs_setup: true });
    window.pywebview = { api: { get_setup_status: getStatus } };
    const { result, unmount } = renderHook(() => useFirstRunSetup(true, false));
    await act(async () => { await vi.advanceTimersByTimeAsync(6000); });
    expect(result.current[0]).toBe(true);
    expect(getStatus).toHaveBeenCalledTimes(3);
    unmount();
    await vi.advanceTimersByTimeAsync(6000);
    expect(getStatus).toHaveBeenCalledTimes(3);
});

it('does not query setup in a browser or after unmount', async () => {
    vi.useFakeTimers();
    const getStatus = vi.fn().mockResolvedValue({ success: false });
    window.pywebview = { api: { get_setup_status: getStatus } };
    const browser = renderHook(() => useFirstRunSetup(true, true));
    expect(getStatus).not.toHaveBeenCalled();
    browser.unmount();
    const desktop = renderHook(() => useFirstRunSetup(true, false));
    await act(async () => { await Promise.resolve(); });
    desktop.unmount();
    await vi.advanceTimersByTimeAsync(6000);
    expect(getStatus).toHaveBeenCalledTimes(1);
});
