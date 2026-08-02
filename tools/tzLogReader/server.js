import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { extname, join, normalize } from "node:path";
import { spawn } from "node:child_process";

const PORT = Number(process.env.PORT || 8765);
const ROOT = process.cwd();
const PUBLIC_DIR = join(ROOT, "web");

function parseCommandString(command) {
  const parts = [];
  let current = "";
  let quote = "";

  for (const character of command) {
    if (quote) {
      if (character === quote) {
        quote = "";
      } else {
        current += character;
      }
      continue;
    }

    if (character === '"' || character === "'") {
      quote = character;
      continue;
    }

    if (character === " " || character === "\t") {
      if (current) {
        parts.push(current);
        current = "";
      }
      continue;
    }

    current += character;
  }

  if (current) {
    parts.push(current);
  }

  return parts;
}

function resolvePythonCommand() {
  const configured = process.env.PYTHON || process.env.PYTHON_BIN;
  if (configured && configured.trim()) {
    const [command, ...args] = parseCommandString(configured.trim());
    if (command) {
      return [command, ...args];
    }
  }
  return process.platform === "win32" ? ["python"] : ["python3"];
}

const MIME_TYPES = {
  ".html": "text/html; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
};

function sendJson(res, status, body) {
  res.writeHead(status, { "content-type": "application/json; charset=utf-8" });
  res.end(JSON.stringify(body));
}

function readRequestBody(req) {
  return new Promise((resolve, reject) => {
    let body = "";
    req.setEncoding("utf8");
    req.on("data", (chunk) => {
      body += chunk;
      if (body.length > 5_000_000) {
        reject(new Error("Request body is too large"));
        req.destroy();
      }
    });
    req.on("end", () => resolve(body));
    req.on("error", reject);
  });
}

function runPythonBridge(payload) {
  return new Promise((resolve, reject) => {
    const [pythonExecutable, ...pythonArgs] = resolvePythonCommand();
    const child = spawn(pythonExecutable, [...pythonArgs, "web_bridge.py"], { cwd: ROOT, stdio: ["pipe", "pipe", "pipe"] });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk) => {
      stdout += chunk.toString();
    });
    child.stderr.on("data", (chunk) => {
      stderr += chunk.toString();
    });
    child.on("error", reject);
    child.on("close", (code) => {
      if (code !== 0) {
        reject(new Error(stderr || `Python bridge exited with code ${code}`));
        return;
      }
      try {
        resolve(JSON.parse(stdout));
      } catch (error) {
        reject(new Error(`Invalid Python bridge response: ${error.message}\n${stdout}\n${stderr}`));
      }
    });
    child.stdin.end(JSON.stringify(payload));
  });
}

async function serveStatic(req, res) {
  const url = new URL(req.url, `http://${req.headers.host}`);
  const requested = url.pathname === "/" ? "/index.html" : decodeURIComponent(url.pathname);
  const safePath = normalize(requested).replace(/^(\.\.[/\\])+/, "");
  const filePath = join(PUBLIC_DIR, safePath);
  try {
    const body = await readFile(filePath);
    res.writeHead(200, { "content-type": MIME_TYPES[extname(filePath)] || "application/octet-stream" });
    res.end(body);
  } catch {
    res.writeHead(404, { "content-type": "text/plain; charset=utf-8" });
    res.end("Not found");
  }
}

const server = createServer(async (req, res) => {
  try {
    if (req.method === "POST" && req.url === "/api") {
      const raw = await readRequestBody(req);
      const payload = raw ? JSON.parse(raw) : {};
      const result = await runPythonBridge(payload);
      sendJson(res, result.ok ? 200 : 400, result);
      return;
    }
    if (req.method === "GET") {
      await serveStatic(req, res);
      return;
    }
    sendJson(res, 405, { ok: false, error: "Method not allowed" });
  } catch (error) {
    sendJson(res, 500, { ok: false, error: error.message });
  }
});

server.listen(PORT, "127.0.0.1", () => {
  console.log(`tzlog Reader web UI: http://127.0.0.1:${PORT}`);
});
