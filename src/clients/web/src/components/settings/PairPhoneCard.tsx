import { useCallback, useEffect, useState } from 'react';
import { QRCodeSVG } from 'qrcode.react';
import { Smartphone, RefreshCw, AlertTriangle, Eye, EyeOff } from 'lucide-react';
import { isBrowserTransport } from '../../transport/pywebviewShim';
import './PairPhoneCard.css';

/**
 * "Pair your phone" — a QR code the phone camera scans to connect in one step.
 *
 * The link carries the Brain URL and the server token as query params; the client
 * consumes and strips them from the address bar on arrival
 * (`transportConfig.ingestQueryParams`), so the token doesn't sit in browser history.
 *
 * Desktop GUI only, and deliberately so: the QR encodes the credential that unlocks the
 * Brain, so it's fetched over the in-process pywebview bridge (`get_pairing_info`) rather
 * than any HTTP route. Nothing on the network can ask for this. It also means the code is
 * only ever displayed on the machine that already holds the token — rendering it in the
 * browser client would be handing the secret to whoever already needed it, or worse.
 *
 * The QR is hidden behind an explicit reveal. It's a credential on screen, and Matthew
 * streams/screenshares from this machine — one click is a cheap guard against it being
 * visible by default in Settings.
 */

interface Candidate {
    label: string;
    base: string;
    pairing_url: string;
    installable: boolean;
}

interface PairingInfo {
    success: boolean;
    error?: string;
    candidates: Candidate[];
}

export function PairPhoneCard() {
    const [info, setInfo] = useState<PairingInfo | null>(null);
    const [loading, setLoading] = useState(false);
    const [selected, setSelected] = useState(0);
    const [revealed, setRevealed] = useState(false);

    const load = useCallback(async () => {
        const api = window.pywebview?.api;
        if (!api || typeof api.get_pairing_info !== 'function') {
            setInfo({ success: false, error: 'Pairing is only available in the desktop app.', candidates: [] });
            return;
        }
        setLoading(true);
        try {
            const result = await api.get_pairing_info() as PairingInfo;
            setInfo(result);
            setSelected(0);
        } catch (e) {
            console.error('[PairPhoneCard] get_pairing_info failed:', e);
            setInfo({ success: false, error: 'Could not read pairing info.', candidates: [] });
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => {
        if (!isBrowserTransport()) void load();
    }, [load]);

    // Browser/PWA client: it already IS the paired phone, and it must never render the token.
    if (isBrowserTransport()) return null;

    const candidate = info?.candidates?.[selected];

    return (
        <div className="pair-phone">
            <div className="pair-phone__head">
                <Smartphone size={17} />
                <div>
                    <div className="pair-phone__title">Pair your phone</div>
                    <div className="pair-phone__sub">
                        Scan with your phone camera to connect — no typing the token.
                    </div>
                </div>
                <button className="pair-phone__refresh" onClick={() => void load()} disabled={loading} title="Refresh">
                    <RefreshCw size={14} className={loading ? 'pair-phone__spin' : undefined} />
                </button>
            </div>

            {info && !info.success && (
                <div className="pair-phone__error">
                    <AlertTriangle size={15} />
                    <span>{info.error}</span>
                </div>
            )}

            {info?.success && candidate && (
                <>
                    {info.candidates.length > 1 && (
                        <div className="pair-phone__options">
                            {info.candidates.map((c, i) => (
                                <button
                                    key={c.base}
                                    className={`pair-phone__option ${i === selected ? 'is-active' : ''}`}
                                    onClick={() => setSelected(i)}
                                >
                                    {c.label}
                                </button>
                            ))}
                        </div>
                    )}

                    {!candidate.installable && (
                        <div className="pair-phone__warn">
                            This address is plain HTTP, so the phone can open it in a browser but
                            can&apos;t install it as an app. Run <code>tailscale serve</code> for that.
                        </div>
                    )}

                    <div className="pair-phone__qr-wrap">
                        {revealed ? (
                            <div className="pair-phone__qr">
                                <QRCodeSVG value={candidate.pairing_url} size={188} level="M" marginSize={2} />
                            </div>
                        ) : (
                            <button className="pair-phone__reveal" onClick={() => setRevealed(true)}>
                                <Eye size={16} />
                                Show QR code
                                <span className="pair-phone__reveal-hint">
                                    Contains your server token — don&apos;t show it on a stream
                                </span>
                            </button>
                        )}
                    </div>

                    {revealed && (
                        <button className="pair-phone__hide" onClick={() => setRevealed(false)}>
                            <EyeOff size={13} />
                            Hide
                        </button>
                    )}

                    <div className="pair-phone__addr">{candidate.base}</div>
                </>
            )}
        </div>
    );
}

export default PairPhoneCard;
