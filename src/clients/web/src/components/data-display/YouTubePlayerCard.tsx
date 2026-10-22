import React, { useState } from 'react';
import { Play, X, Youtube } from 'lucide-react';
import './YouTubePlayerCard.css';

interface YouTubePlayerCardProps {
    id: string;
    title: string;
}

const YouTubePlayerCard: React.FC<YouTubePlayerCardProps> = ({ id, title }) => {
    const [isPlaying, setIsPlaying] = useState(false);

    // Get the high-resolution thumbnail URL
    const maxResUrl = `https://img.youtube.com/vi/${id}/maxresdefault.jpg`;
    
    // Fallback thumbnail URL in case maxres is not available
    const fallbackThumbnailUrl = `https://img.youtube.com/vi/${id}/hqdefault.jpg`;

    const [prevId, setPrevId] = useState(id);
    const [imgSrc, setImgSrc] = useState(maxResUrl);

    if (id !== prevId) {
        setPrevId(id);
        setImgSrc(maxResUrl);
    }

    const handlePlayClick = (e: React.MouseEvent) => {
        e.stopPropagation();
        setIsPlaying(true);
    };

    const handleCloseClick = (e: React.MouseEvent) => {
        e.stopPropagation();
        setIsPlaying(false);
    };

    const handleImageLoad = (e: React.SyntheticEvent<HTMLImageElement, Event>) => {
        const img = e.currentTarget;
        // YouTube returns a 120x90 default placeholder image when maxresdefault is not available
        if (img.naturalWidth === 120 && img.naturalHeight === 90) {
            if (imgSrc !== fallbackThumbnailUrl) {
                setImgSrc(fallbackThumbnailUrl);
            }
        }
    };

    const handleImageError = () => {
        if (imgSrc !== fallbackThumbnailUrl) {
            setImgSrc(fallbackThumbnailUrl);
        }
    };

    return (
        <div className={`youtube-player-card ${isPlaying ? 'playing' : 'preview'}`}>
            {/* Minimal/Muted Top Header */}
            <div className="youtube-card-header">
                <div className="youtube-header-left">
                    <Youtube className="youtube-brand-icon" size={16} />
                    <span className="youtube-video-title" title={title}>
                        {title}
                    </span>
                </div>
                {isPlaying && (
                    <button 
                        className="youtube-close-button" 
                        onClick={handleCloseClick}
                        aria-label="Close Video Player"
                    >
                        <X size={14} />
                    </button>
                )}
            </div>

            {/* Main Widescreen Content Area */}
            <div className="youtube-card-body">
                {!isPlaying ? (
                    <div className="youtube-thumbnail-wrapper" onClick={handlePlayClick}>
                        <img 
                            src={imgSrc} 
                            alt={title}
                            className="youtube-video-thumbnail"
                            onLoad={handleImageLoad}
                            onError={handleImageError}
                        />
                        {/* Glassmorphism Hover Overlay */}
                        <div className="youtube-hover-overlay">
                            <div className="youtube-play-btn-glow">
                                <Play className="youtube-play-icon" size={24} fill="currentColor" />
                            </div>
                        </div>
                    </div>
                ) : (
                    <div className="youtube-iframe-wrapper">
                        <iframe
                            width="100%"
                            height="100%"
                            src={`https://www.youtube.com/embed/${id}?autoplay=1&rel=0&modestbranding=1`}
                            title={title}
                            frameBorder="0"
                            allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share"
                            allowFullScreen
                            className="youtube-iframe"
                        ></iframe>
                    </div>
                )}
            </div>
        </div>
    );
};

export default YouTubePlayerCard;
