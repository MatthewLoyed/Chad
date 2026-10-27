/**
 * Browser command client (#188).
 * ==============================
 *
 * TS port of the desktop `RemoteCommandHandler.execute`
 * (src/clients/desktop/api/base.py): serialize an application command to
 * `{command_class, command_args}` and POST it to `{base}/api/command` with the
 * `x-chad-token` header. Returns the `.result` payload the Brain sends back.
 *
 * The desktop handler relays a Command *dataclass*; here the caller (the shim)
 * passes the class name and the already-built args dict, since the browser has no
 * Python dataclasses. Field names mirror the command constructors invoked by the
 * desktop API mixins.
 */
import { getServerUrl, getToken, toHttpBase } from './transportConfig';

const COMMAND_TIMEOUT_MS = 15000;

export class CommandError extends Error {
    readonly status?: number;
    constructor(message: string, status?: number) {
        super(message);
        this.name = 'CommandError';
        this.status = status;
    }
}

/**
 * Relay a single command to the Brain. `commandClass` is the Python command
 * class name (e.g. "GetConversationsCommand"); `args` are its constructor kwargs.
 */
export async function executeCommand<T = unknown>(
    commandClass: string,
    args: Record<string, unknown> = {},
): Promise<T> {
    const timeoutMs = commandClass === 'SetActiveModelCommand' ? 420000 : COMMAND_TIMEOUT_MS;
    const base = toHttpBase(getServerUrl());
    const token = getToken();
    const url = `${base}/api/command`;

    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);

    try {
        const resp = await fetch(url, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                ...(token ? { 'x-chad-token': token } : {}),
            },
            body: JSON.stringify({ command_class: commandClass, command_args: args }),
            signal: controller.signal,
        });

        if (!resp.ok) {
            const text = await resp.text().catch(() => '');
            throw new CommandError(`HTTP ${resp.status} from Brain: ${text}`, resp.status);
        }

        const data = (await resp.json()) as { result?: T };
        return data.result as T;
    } catch (err) {
        if (err instanceof CommandError) throw err;
        if (err instanceof DOMException && err.name === 'AbortError') {
            throw new CommandError(`Command '${commandClass}' timed out`);
        }
        throw new CommandError(
            `Command '${commandClass}' failed: ${err instanceof Error ? err.message : String(err)}`,
        );
    } finally {
        clearTimeout(timer);
    }
}
