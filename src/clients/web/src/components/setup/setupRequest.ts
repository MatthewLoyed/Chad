/** Setup requests must settle so a missing desktop response cannot trap the user. */
export const SETUP_REQUEST_TIMEOUT_MS = 15000;

export function setupRequest<T>(request: Promise<T>): Promise<T> {
    return new Promise((resolve, reject) => {
        const timer = setTimeout(() => reject(new Error('Chad took too long to respond. Try again.')), SETUP_REQUEST_TIMEOUT_MS);
        request.then(value => { clearTimeout(timer); resolve(value); },
            error => { clearTimeout(timer); reject(error); });
    });
}
