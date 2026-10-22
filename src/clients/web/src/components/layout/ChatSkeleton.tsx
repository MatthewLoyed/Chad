import './ChatSkeleton.css';

/**
 * Modern chat skeleton loader representing an ongoing conversation.
 * Displayed during cold conversation switching to prevent blank screens or layout shifts.
 */
export default function ChatSkeleton() {
    return (
        <div className="chat-skeleton-stream" aria-busy="true" aria-label="Loading conversation">
            {/* User message placeholder */}
            <div className="chat-skeleton-item chat-skeleton-item--user">
                <div className="chat-skeleton-bubble chat-skeleton-bubble--user">
                    <div className="chat-skeleton-line chat-skeleton-line--short" style={{ width: '180px' }} />
                    <div className="chat-skeleton-line chat-skeleton-line--medium" style={{ width: '120px' }} />
                </div>
            </div>

            {/* Assistant message placeholder */}
            <div className="chat-skeleton-item">
                <div className="chat-skeleton-avatar-header">
                    <div className="chat-skeleton-avatar" />
                    <div className="chat-skeleton-name" />
                </div>
                <div className="chat-skeleton-bubble chat-skeleton-bubble--ai">
                    <div className="chat-skeleton-line chat-skeleton-line--long" />
                    <div className="chat-skeleton-line chat-skeleton-line--full" />
                    <div className="chat-skeleton-line chat-skeleton-line--medium" />
                </div>
            </div>

            {/* Second User message placeholder */}
            <div className="chat-skeleton-item chat-skeleton-item--user">
                <div className="chat-skeleton-bubble chat-skeleton-bubble--user">
                    <div className="chat-skeleton-line chat-skeleton-line--short" style={{ width: '150px' }} />
                </div>
            </div>

            {/* Second Assistant message placeholder */}
            <div className="chat-skeleton-item">
                <div className="chat-skeleton-avatar-header">
                    <div className="chat-skeleton-avatar" />
                    <div className="chat-skeleton-name" />
                </div>
                <div className="chat-skeleton-bubble chat-skeleton-bubble--ai">
                    <div className="chat-skeleton-line chat-skeleton-line--long" style={{ width: '88%' }} />
                    <div className="chat-skeleton-line chat-skeleton-line--short" style={{ width: '45%' }} />
                </div>
            </div>
        </div>
    );
}
