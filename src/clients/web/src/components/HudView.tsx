import React, { useState, useEffect, useRef } from 'react';
import type { ThinkingEvent } from '../store';
import './HudView.css';

/**
 * Chad HUD (Heads-Up Display) Component
 * A minimalist, bottom-middle command bar with purple pulse animations.
 * Designed for quick interactions via Alt+Space.
 */
const HudView: React.FC = () => {
  const [inputValue, setInputValue] = useState('');
  const [isProcessing, setIsProcessing] = useState(false);
  const [result, setResult] = useState<string | null>(null);
  const [isClosing, setIsClosing] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Handle Electron-driven fade out
  useEffect(() => {
    // Only attempt if we are in an Electron environment
    const electron = (window.require ? window.require('electron') : null) as {
      ipcRenderer?: {
        send: (channel: string, ...args: unknown[]) => void;
        on: (channel: string, listener: (...args: unknown[]) => void) => void;
        removeListener: (channel: string, listener: (...args: unknown[]) => void) => void;
      };
    } | null;
    
    if (electron && electron.ipcRenderer) {
      const ipc = electron.ipcRenderer;
      const handleFadeOut = () => {
        setIsClosing(true);
        // Duration should match CSS animation (400ms)
        setTimeout(() => {
          ipc.send('hud-fade-out-complete');
          setIsClosing(false);
        }, 400);
      };

      ipc.on('hud-fade-out', handleFadeOut);
      return () => {
        ipc.removeListener('hud-fade-out', handleFadeOut);
      };
    }
  }, []);

  // Focus input and manage body class
  useEffect(() => {
    if (inputRef.current) {
      inputRef.current.focus();
    }
    
    // Force total transparency to remove the "box" effect from index.css
    document.documentElement.classList.add('is-hud');
    document.body.classList.add('is-hud');
    
    return () => {
      document.documentElement.classList.remove('is-hud');
      document.body.classList.remove('is-hud');
    };
  }, []);

  // Handle command submission
  const handleKeyDown = async (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && inputValue.trim()) {
      setIsProcessing(true);
      setResult(null);
      
      const api = window.pywebview?.api;
      if (api && typeof api.send_text_command === 'function') {
        try {
          // Send the command to the Brain.
          // Note: The backend will be updated to handle the 'origin' metadata.
          await api.send_text_command(inputValue.trim(), null, null, null, { origin: 'hud' });
          setInputValue('');
        } catch (err) {
          console.error('HUD: Command submission failed', err);
          setIsProcessing(false);
          setResult('Error sending command.');
        }
      } else {
        // Mock success for development if API is missing
        console.warn('HUD: API not found, simulating success');
        setTimeout(() => {
          setIsProcessing(false);
          setResult('Chad is ready (Simulated)');
        }, 1500);
      }
    } else if (e.key === 'Escape') {
      // Optional: Close on Escape if the backend doesn't handle it
      const api = window.pywebview?.api;
      if (api && typeof api.hide_hud === 'function') {
        api.hide_hud();
      }
    }
  };

  // Listen for updates from the Brain
  useEffect(() => {
    // 1. Handle Thinking Events (Pulse Control)
    const handleThinkingEvent = (event: ThinkingEvent) => {
      if (event.type === 'tool_start') {
        setIsProcessing(true);
        setResult(null);
      } else if (event.type === 'turn_complete') {
        setIsProcessing(false);
      }
    };

    // 2. Handle Final Responses
    const addAIMessageToChat = (text: string) => {
      // Extract text if it's a complex object
      let cleanText = text;
      if (typeof text !== 'string') {
        try { cleanText = JSON.stringify(text); } catch { cleanText = 'Response received'; }
      }
      
      setResult(cleanText);
      setIsProcessing(false);

      // Auto-hide the result after 5 seconds
      if (timerRef.current) clearTimeout(timerRef.current);
      timerRef.current = setTimeout(() => {
        setResult(null);
        // We don't hide the window here, just clear the text so it's ready for next input
      }, 5000);
    };

    // 3. Handle status text (e.g. "Searching...")
    const handleStatusText = (text: string) => {
      if (isProcessing) {
        setResult(text); // Show progress status in the HUD
      }
    };

    // Register globally for Python bridge
    window.handleThinkingEvent = handleThinkingEvent;
    window.addAIMessageToChat = addAIMessageToChat;
    window.handleStatusText = handleStatusText;

    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
      // Clean up window globals (careful not to break main app if they share process)
    };
  }, [isProcessing]);

  return (
    <div className={`hud-root ${isProcessing ? 'is-processing' : ''} ${result ? 'has-result' : ''} ${isClosing ? 'is-closing' : ''}`}>
      <div className="hud-container">
        {/* The Purple Glow (Pulsing during processing) */}
        <div className="hud-glow" />
        
        <div className="hud-content">
          {!isProcessing && !result && (
            <div className="hud-input-wrapper">
              <span className="hud-prompt">/</span>
              <input
                ref={inputRef}
                className="hud-input"
                placeholder="How can Chad help?"
                value={inputValue}
                onChange={(e) => setInputValue(e.target.value)}
                onKeyDown={handleKeyDown}
              />
            </div>
          )}

          {isProcessing && (
            <div className="hud-status">
              <div className="hud-spinner">
                <div className="spinner-inner"></div>
              </div>
              <span className="hud-status-text">Thinking...</span>
            </div>
          )}

          {result && !isProcessing && (
            <div className="hud-result-view">
              <span className="hud-result-text">{result}</span>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default HudView;
