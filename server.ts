import express, { Request, Response } from 'express';
import { createServer as createViteServer } from 'vite';
import { exec } from 'child_process';
import { promisify } from 'util';
import path from 'path';
import fs from 'fs';
import dotenv from 'dotenv';

import { fileURLToPath } from 'url';

dotenv.config();

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const execAsync = promisify(exec);
const app = express();
const PORT = process.env.PORT ? parseInt(process.env.PORT, 10) : 3000;

app.use(express.json());

const SOURCE_DIR = path.resolve(__dirname, 'sanour_ai_source');
const PYTHON_PATH = '/tmp/venv/bin/python3';
const PYTEST_PATH = '/tmp/venv/bin/pytest';
const RUFF_PATH = '/tmp/venv/bin/ruff';

// Status API
app.get('/api/status', async (_req: Request, res: Response) => {
  try {
    const pyVersion = await execAsync(`${PYTHON_PATH} --version`).then(r => r.stdout.trim()).catch(() => 'Not found');
    const pytestVersion = await execAsync(`${PYTEST_PATH} --version`).then(r => r.stdout.trim()).catch(() => 'Not found');
    const sourceExists = fs.existsSync(SOURCE_DIR);

    res.json({
      status: 'ready',
      pythonVersion: pyVersion,
      pytestVersion: pytestVersion,
      sourceDirectory: SOURCE_DIR,
      sourceExists,
      totalExpectedTests: 230,
      timestamp: new Date().toISOString()
    });
  } catch (err: any) {
    res.status(500).json({ status: 'error', message: err.message });
  }
});

// Run Tests API
app.post('/api/run-tests', async (req: Request, res: Response) => {
  const { testFile, keyword, flags } = req.body || {};
  const startTime = Date.now();

  try {
    let target = `${SOURCE_DIR}/tests`;
    if (testFile) {
      const sanitized = testFile.replace(/\.\./g, '');
      target = path.join(SOURCE_DIR, 'tests', sanitized);
    }

    let cmd = `${PYTEST_PATH} "${target}" -v`;
    if (keyword) {
      cmd += ` -k "${keyword.replace(/"/g, '')}"`;
    }
    if (flags) {
      cmd += ` ${flags}`;
    }

    const { stdout, stderr } = await execAsync(cmd, {
      cwd: SOURCE_DIR,
      env: { ...process.env, PYTHONPATH: `${SOURCE_DIR}/src:${SOURCE_DIR}` },
      timeout: 60000
    });

    const duration = ((Date.now() - startTime) / 1000).toFixed(2);
    const output = stdout + (stderr ? `\nSTDERR:\n${stderr}` : '');

    // Parse summary line like "230 passed, 18 subtests passed in 5.88s"
    const passedMatch = output.match(/(\d+)\s+passed/);
    const skippedMatch = output.match(/(\d+)\s+skipped/);
    const failedMatch = output.match(/(\d+)\s+failed/);
    const errorsMatch = output.match(/(\d+)\s+errors?/);
    const subtestsMatch = output.match(/(\d+)\s+subtests passed/);

    const passed = passedMatch ? parseInt(passedMatch[1], 10) : 0;
    const skipped = skippedMatch ? parseInt(skippedMatch[1], 10) : 0;
    const failed = failedMatch ? parseInt(failedMatch[1], 10) : 0;
    const errors = errorsMatch ? parseInt(errorsMatch[1], 10) : 0;
    const subtests = subtestsMatch ? parseInt(subtestsMatch[1], 10) : 0;

    res.json({
      success: true,
      exitCode: 0,
      duration: `${duration}s`,
      summary: {
        passed,
        skipped,
        failed,
        errors,
        subtests,
        total: passed + skipped + failed + errors
      },
      rawOutput: output
    });
  } catch (err: any) {
    const duration = ((Date.now() - startTime) / 1000).toFixed(2);
    const output = (err.stdout || '') + (err.stderr ? `\nSTDERR:\n${err.stderr}` : '') + `\n${err.message}`;

    const passedMatch = output.match(/(\d+)\s+passed/);
    const failedMatch = output.match(/(\d+)\s+failed/);
    const errorsMatch = output.match(/(\d+)\s+errors?/);
    const subtestsMatch = output.match(/(\d+)\s+subtests passed/);
    const skippedMatch = output.match(/(\d+)\s+skipped/);

    const passed = passedMatch ? parseInt(passedMatch[1], 10) : 0;
    const failed = failedMatch ? parseInt(failedMatch[1], 10) : 0;
    const errors = errorsMatch ? parseInt(errorsMatch[1], 10) : 0;
    const skipped = skippedMatch ? parseInt(skippedMatch[1], 10) : 0;
    const subtests = subtestsMatch ? parseInt(subtestsMatch[1], 10) : 0;

    res.json({
      success: false,
      exitCode: err.code || 1,
      duration: `${duration}s`,
      summary: {
        passed,
        failed,
        errors,
        skipped,
        subtests,
        total: passed + failed + errors + skipped
      },
      rawOutput: output
    });
  }
});

// Run Ruff Lint API
app.post('/api/run-lint', async (_req: Request, res: Response) => {
  try {
    const { stdout, stderr } = await execAsync(`${RUFF_PATH} check .`, {
      cwd: SOURCE_DIR
    }).catch(err => ({ stdout: err.stdout || '', stderr: err.stderr || err.message }));

    const output = stdout + (stderr ? `\n${stderr}` : '');
    const errorsCount = (output.match(/Found \d+ errors/i) || [''])[0];

    res.json({
      success: true,
      rawOutput: output,
      summary: errorsCount || 'No issues found'
    });
  } catch (err: any) {
    res.status(500).json({ success: false, error: err.message });
  }
});

// Get all test files dynamically
app.get('/api/test-files', (_req: Request, res: Response) => {
  try {
    const testsDir = path.join(SOURCE_DIR, 'tests');
    const getPyTests = (dir: string, base = ''): string[] => {
      let results: string[] = [];
      const list = fs.readdirSync(dir, { withFileTypes: true });
      for (const item of list) {
        if (item.name.startsWith('.') || item.name === '__pycache__') continue;
        const rel = base ? `${base}/${item.name}` : item.name;
        if (item.isDirectory()) {
          results = results.concat(getPyTests(path.join(dir, item.name), rel));
        } else if (item.name.startsWith('test_') && item.name.endsWith('.py')) {
          results.push(rel);
        }
      }
      return results;
    };
    const files = getPyTests(testsDir);
    res.json({ success: true, files });
  } catch (err: any) {
    res.status(500).json({ success: false, error: err.message });
  }
});

// Update Source from GitHub Zip URL
app.post('/api/update-source', async (req: Request, res: Response) => {
  const { zipUrl } = req.body || {};
  if (!zipUrl || typeof zipUrl !== 'string') {
    return res.status(400).json({ success: false, error: 'آدرس فایل ZIP معتبر نیست' });
  }

  try {
    let rawUrl = zipUrl.trim();
    if (rawUrl.includes('github.com') && rawUrl.includes('/blob/')) {
      rawUrl = rawUrl.replace('github.com', 'raw.githubusercontent.com').replace('/blob/', '/');
    }

    const tmpDir = `/tmp/sanour_update_${Date.now()}`;
    const zipPath = `${tmpDir}/update.zip`;

    await execAsync(`mkdir -p "${tmpDir}"`);
    await execAsync(`curl -sSL "${rawUrl}" -o "${zipPath}"`);
    await execAsync(`unzip -q "${zipPath}" -d "${tmpDir}/extracted"`);

    // Find directory containing pyproject.toml
    const findCmd = `find "${tmpDir}/extracted" -name "pyproject.toml" | head -n 1`;
    const { stdout: foundPath } = await execAsync(findCmd);
    if (!foundPath.trim()) {
      return res.status(400).json({ success: false, error: 'فایل pyproject.toml در پکیج دریافتی یافت نشد' });
    }

    const packageRoot = path.dirname(foundPath.trim());
    
    // Replace source safely
    await execAsync(`rm -rf "${SOURCE_DIR}" && cp -r "${packageRoot}" "${SOURCE_DIR}"`);
    await execAsync(`rm -rf "${tmpDir}"`);

    // Run pytest on the new source immediately
    const { stdout: testOut } = await execAsync(`${PYTEST_PATH} "${SOURCE_DIR}/tests" -v`, {
      cwd: SOURCE_DIR,
      env: { ...process.env, PYTHONPATH: `${SOURCE_DIR}/src:${SOURCE_DIR}` },
      timeout: 60000
    }).catch(err => ({ stdout: (err.stdout || '') + '\n' + (err.stderr || '') }));

    res.json({
      success: true,
      message: 'نسخه جدید با موفقیت دریافت و جایگزین شد',
      testOutput: testOut
    });
  } catch (err: any) {
    res.status(500).json({ success: false, error: err.message });
  }
});

// File Tree & Viewer
app.get('/api/files', (_req: Request, res: Response) => {
  const getFiles = (dir: string, base: string = ''): any[] => {
    const entries = fs.readdirSync(dir, { withFileTypes: true });
    return entries
      .filter(e => !e.name.startsWith('.') && e.name !== '__pycache__')
      .map(e => {
        const rel = path.join(base, e.name);
        const full = path.join(dir, e.name);
        if (e.isDirectory()) {
          return { name: e.name, path: rel, type: 'directory', children: getFiles(full, rel) };
        }
        return { name: e.name, path: rel, type: 'file', size: fs.statSync(full).size };
      });
  };

  try {
    const tree = getFiles(SOURCE_DIR);
    res.json({ success: true, files: tree });
  } catch (err: any) {
    res.status(500).json({ success: false, error: err.message });
  }
});

app.get('/api/file-content', (req: Request, res: Response) => {
  const filePath = req.query.path as string;
  if (!filePath) return res.status(400).json({ error: 'Missing path' });

  const safePath = path.resolve(SOURCE_DIR, filePath.replace(/\.\./g, ''));
  if (!safePath.startsWith(SOURCE_DIR)) {
    return res.status(403).json({ error: 'Forbidden' });
  }

  try {
    if (!fs.existsSync(safePath)) return res.status(404).json({ error: 'Not found' });
    const content = fs.readFileSync(safePath, 'utf-8');
    res.json({ success: true, content, path: filePath });
  } catch (err: any) {
    res.status(500).json({ success: false, error: err.message });
  }
});

// Vite or static mounting
async function startServer() {
  const isDev = process.env.NODE_ENV !== 'production';

  if (isDev) {
    const vite = await createViteServer({
      server: { middlewareMode: true },
      appType: 'spa',
    });
    app.use(vite.middlewares);
  } else {
    app.use(express.static(path.resolve(__dirname, 'dist')));
    app.get('*', (_req: Request, res: Response) => {
      res.sendFile(path.resolve(__dirname, 'dist', 'index.html'));
    });
  }

  app.listen(PORT, '0.0.0.0', () => {
    console.log(`SANOUR AI Server listening on http://0.0.0.0:${PORT}`);
  });
}

startServer();
