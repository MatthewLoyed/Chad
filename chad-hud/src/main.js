import { app, BrowserWindow, globalShortcut, screen, ipcMain } from 'electron';
import path from 'node:path';

// Disable GPU acceleration if needed for transparent windows on some Windows builds
app.disableHardwareAcceleration();

const createWindow = () => {
  const primaryDisplay = screen.getPrimaryDisplay();
  const { width: screenWidth, height: screenHeight } = primaryDisplay.workArea;
  
  const windowWidth = 800;
  const windowHeight = 160; // Increased height to prevent bottom clipping
  
  const x = Math.floor((screenWidth - windowWidth) / 2);
  const y = Math.floor(screenHeight - windowHeight - 40); // 40px padding from taskbar

  const win = new BrowserWindow({
    width: windowWidth,
    height: windowHeight,
    x: x,
    y: y,
    frame: false,
    transparent: true,
    alwaysOnTop: true,
    skipTaskbar: true,
    resizable: false,
    movable: true,
    hasShadow: false,
    backgroundColor: '#00000000', // fully transparent ARGB
    webPreferences: {
      nodeIntegration: true,
      contextIsolation: false,
      preload: path.join(__dirname, 'preload.js'),
    },
  });

  // Load the React HUD URL
  win.loadURL('http://localhost:5173/?mode=hud');

  // Start hidden
  win.hide();

  // Handle global hotkey Alt+Space
  const toggleHUD = () => {
    if (win.isVisible()) {
      win.webContents.send('hud-fade-out');
    } else {
      win.show();
      win.focus();
    }
  };

  // Primary Hotkey: Alt+Space
  let isPrimaryRegistered = globalShortcut.register('Alt+Space', toggleHUD);
  
  if (!isPrimaryRegistered) {
    console.warn('[ HUD ] : Alt+Space is already registered by another application (possibly Windows system menu or PowerToys).');
    // Try a fallback hotkey
    let isFallbackRegistered = globalShortcut.register('Control+Alt+Space', toggleHUD);
    if (isFallbackRegistered) {
      console.info('[ HUD ] : Using fallback hotkey: Control+Alt+Space');
    } else {
      console.error('[ HUD ] : Registration failed for all hotkeys. HUD will not be toggleable via keyboard.');
    }
  } else {
    console.info('[ HUD ] : Primary hotkey Alt+Space registered successfully.');
  }

  // Listen for animation completion from renderer
  ipcMain.on('hud-fade-out-complete', () => {
    win.hide();
  });

  // Prevent window from being closed by the user
  win.on('close', (event) => {
    event.preventDefault();
    win.hide();
  });
};

app.whenReady().then(() => {
  createWindow();
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

// Clean up shortcuts on quit
app.on('will-quit', () => {
  globalShortcut.unregisterAll();
});
