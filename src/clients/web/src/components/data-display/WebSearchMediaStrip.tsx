import { SiYoutube, SiVimeo } from 'react-icons/si';
import { FaPlay } from 'react-icons/fa';
import type { SourceMediaImage, SourceMediaVideo } from '../../utils/sources';
import './WebSearchMediaStrip.css';

function VideoIcon({ platform }: { platform?: string }) {
    if (platform === 'youtube') return <SiYoutube color="#FF0000" size={13} />;
    if (platform === 'vimeo') return <SiVimeo color="#1AB7EA" size={13} />;
    return <FaPlay size={11} />;
}

interface WebSearchMediaStripProps {
    media: { images: SourceMediaImage[]; videos: SourceMediaVideo[] };
}

/** Turn-level image strip + video chips, aggregated from a turn's `web_search` sources (issue #331/#334). */
function WebSearchMediaStrip({ media }: WebSearchMediaStripProps) {
    if (media.images.length === 0 && media.videos.length === 0) return null;

    return (
        <div className="web-search-media">
            {media.images.length > 0 && (
                <div className="web-search-media__images">
                    {media.images.map((img, i) => (
                        <a
                            key={`${img.url}-${i}`}
                            href={img.url}
                            target="_blank"
                            rel="noreferrer"
                            className="web-search-media__image-link"
                            title={img.alt || img.url}
                        >
                            <img
                                src={img.url}
                                alt={img.alt || ''}
                                className="web-search-media__image"
                                loading="lazy"
                            />
                        </a>
                    ))}
                </div>
            )}
            {media.videos.length > 0 && (
                <div className="web-search-media__videos">
                    {media.videos.map((vid, i) => (
                        <a
                            key={`${vid.url}-${i}`}
                            href={vid.url}
                            target="_blank"
                            rel="noreferrer"
                            className="web-search-media__video-chip"
                        >
                            <VideoIcon platform={vid.platform} />
                            <span className="web-search-media__video-title">
                                {vid.title || vid.url}
                            </span>
                        </a>
                    ))}
                </div>
            )}
        </div>
    );
}

export default WebSearchMediaStrip;
