import { createServer } from "node:http";

export function sum(numbers) {
  if (!Array.isArray(numbers) || numbers.some((n) => typeof n !== "number")) {
    throw new TypeError("numbers must be an array of numbers");
  }
  return numbers.reduce((acc, n) => acc + n, 0);
}

export function createApp() {
  return createServer((req, res) => {
    const url = new URL(req.url, "http://localhost");
    const send = (status, body) => {
      res.writeHead(status, { "content-type": "application/json" });
      res.end(JSON.stringify(body));
    };

    if (req.method === "GET" && url.pathname === "/health") {
      return send(200, { status: "ok" });
    }
    if (req.method === "GET" && url.pathname === "/sum") {
      const raw = url.searchParams.get("n") ?? "";
      const numbers = raw.split(",").filter(Boolean).map(Number);
      if (numbers.some(Number.isNaN)) return send(400, { error: "n must be comma separated numbers" });
      return send(200, { result: sum(numbers) });
    }
    return send(404, { error: "not found" });
  });
}
