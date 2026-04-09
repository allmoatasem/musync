const { app, BrowserWindow, ipcMain, dialog } = require('electron')
const path = require('path')
const { spawn } = require('child_process')
const http = require('http')

// ── config ───────────────────────────────────────────────────────────────────

const SERVER_PORT = 7765
const IS_DEV = !app.isPackaged
// --no-server flag: dev script already started Python, skip spawning
const SKIP_SPAWN = process.argv.includes('--no-server')

// ── Python server lifecycle ──────────────────────────────────────────────────

let pythonProcess = null

function getPythonBinary() {
  if (IS_DEV) {
    // In dev, use the system Python with the installed musync package
    return process.platform === 'win32' ? 'python' : 'python3'
  }
  // In production, use the bundled PyInstaller binary
  const ext = process.platform === 'win32' ? '.exe' : ''
  return path.join(process.resourcesPath, 'resources', `musync-server${ext}`)
}

function getServerArgs() {
  if (IS_DEV) {
    return ['-m', 'musync', 'serve', '--port', String(SERVER_PORT)]
  }
  return ['--port', String(SERVER_PORT)]
}

function startPythonServer() {
  const binary = getPythonBinary()
  const args = getServerArgs()

  // Set working directory to the repo root in dev so Python finds the package
  const cwd = IS_DEV
    ? path.join(__dirname, '..', '..') // app/electron/../../ = repo root
    : undefined

  const env = { ...process.env }
  if (IS_DEV) {
    // Make sure the local src/ is on PYTHONPATH
    const srcDir = path.join(__dirname, '..', '..', 'src')
    env.PYTHONPATH = srcDir + (env.PYTHONPATH ? path.delimiter + env.PYTHONPATH : '')
  }

  pythonProcess = spawn(binary, args, { cwd, env, stdio: ['ignore', 'pipe', 'pipe'] })

  pythonProcess.stdout.on('data', (d) => {
    if (IS_DEV) console.log('[python]', d.toString().trim())
  })
  pythonProcess.stderr.on('data', (d) => {
    if (IS_DEV) console.error('[python]', d.toString().trim())
  })
  pythonProcess.on('exit', (code) => {
    if (IS_DEV) console.log('[python] exited with code', code)
    pythonProcess = null
  })
}

function stopPythonServer() {
  if (pythonProcess) {
    pythonProcess.kill()
    pythonProcess = null
  }
}

// ── wait for server to be ready ──────────────────────────────────────────────

function waitForServer(retries = 30, delayMs = 300) {
  return new Promise((resolve, reject) => {
    const attempt = (n) => {
      const req = http.get(`http://127.0.0.1:${SERVER_PORT}/health`, (res) => {
        if (res.statusCode === 200) resolve()
        else if (n > 0) setTimeout(() => attempt(n - 1), delayMs)
        else reject(new Error('Server did not start in time'))
      })
      req.on('error', () => {
        if (n > 0) setTimeout(() => attempt(n - 1), delayMs)
        else reject(new Error('Server did not start in time'))
      })
      req.setTimeout(200, () => req.destroy())
    }
    attempt(retries)
  })
}

// ── window ───────────────────────────────────────────────────────────────────

let mainWindow = null

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1100,
    height: 720,
    minWidth: 800,
    minHeight: 550,
    titleBarStyle: process.platform === 'darwin' ? 'hiddenInset' : 'default',
    backgroundColor: '#1a1a1a',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  })

  if (IS_DEV) {
    mainWindow.loadURL('http://localhost:5173')
    mainWindow.webContents.openDevTools()
  } else {
    mainWindow.loadFile(path.join(__dirname, '..', 'dist', 'index.html'))
  }

  mainWindow.on('closed', () => { mainWindow = null })
}

// ── IPC handlers ─────────────────────────────────────────────────────────────

ipcMain.handle('get-server-port', () => SERVER_PORT)

ipcMain.handle('open-file-dialog', async (_, options = {}) => {
  const result = await dialog.showOpenDialog(mainWindow, {
    properties: ['openFile'],
    filters: [
      { name: 'Music Projects', extensions: ['dorico', 'stf', 'logicx'] },
      { name: 'All Files', extensions: ['*'] },
    ],
    ...options,
  })
  return result.canceled ? null : result.filePaths[0]
})

ipcMain.handle('open-folder-dialog', async () => {
  const result = await dialog.showOpenDialog(mainWindow, {
    properties: ['openDirectory'],
    filters: [{ name: 'Logic Pro Projects', extensions: ['logicx'] }],
  })
  return result.canceled ? null : result.filePaths[0]
})

// ── app lifecycle ─────────────────────────────────────────────────────────────

app.whenReady().then(async () => {
  if (!SKIP_SPAWN) {
    startPythonServer()
  }

  try {
    await waitForServer()
  } catch (e) {
    console.error('Python server failed to start:', e.message)
    // Show window anyway — the UI will show a "server offline" state
  }

  createWindow()

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow()
  })
})

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit()
})

app.on('before-quit', () => {
  stopPythonServer()
})

app.on('will-quit', () => {
  stopPythonServer()
})
