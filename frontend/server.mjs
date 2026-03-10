import { createServer } from 'http';
import next from 'next';
import { WebSocketServer } from 'ws';

const dev = process.env.NODE_ENV !== 'production';
const hostname = '0.0.0.0';
const port = parseInt(process.env.PORT || '5000', 10);

const app = next({ dev, hostname, port });
const handle = app.getRequestHandler();

app.prepare().then(() => {
  const server = createServer((req, res) => {
    handle(req, res);
  });

  const wss = new WebSocketServer({ noServer: true });

  server.on('upgrade', (req, socket, head) => {
    const pathname = new URL(req.url, `http://${req.headers.host}`).pathname;
    if (pathname === '/ws/chat') {
      wss.handleUpgrade(req, socket, head, (clientWs) => {
        let abortController = null;

        const pingInterval = setInterval(() => {
          if (clientWs.readyState === 1) {
            clientWs.ping();
          }
        }, 20000);

        clientWs.on('message', async (data) => {
          const payload = data.toString();
          abortController = new AbortController();
          let eventCount = 0;
          let lastEventType = '';
          let wsState = 'open';

          console.log('[bridge] SSE fetch starting');

          try {
            const res = await fetch('http://localhost:8001/chat', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: payload,
              signal: abortController.signal,
            });

            if (!res.ok) {
              console.log(`[bridge] Backend returned ${res.status}`);
              if (clientWs.readyState === 1) {
                clientWs.send(JSON.stringify({ error: `Backend error: ${res.status}` }));
                clientWs.close();
              }
              clearInterval(pingInterval);
              return;
            }

            console.log('[bridge] SSE stream connected');

            const reader = res.body.getReader();
            const decoder = new TextDecoder();
            let buffer = '';

            const processLine = (line) => {
              if (line.startsWith('data: ')) {
                const eventData = line.slice(6);
                if (eventData === '[DONE]') {
                  eventCount++;
                  lastEventType = '[DONE]';
                  if (clientWs.readyState === 1) {
                    clientWs.send(JSON.stringify({ done: true }));
                  } else {
                    wsState = 'closed-before-done';
                    console.log(`[bridge] WS already closed (state=${clientWs.readyState}) when sending [DONE]`);
                  }
                } else if (eventData.trim()) {
                  eventCount++;
                  try {
                    const parsed = JSON.parse(eventData);
                    if (parsed.done) lastEventType = 'done';
                    else if (parsed.content) lastEventType = 'content';
                    else if (parsed.session_id) lastEventType = 'session_id';
                    else if (parsed.tool_use_start) lastEventType = 'tool_use_start';
                    else if (parsed.tool_result) lastEventType = 'tool_result';
                    else if (parsed.error) lastEventType = 'error';
                    else lastEventType = Object.keys(parsed)[0] || 'unknown';
                  } catch { lastEventType = 'unparsed'; }

                  if (clientWs.readyState === 1) {
                    clientWs.send(eventData);
                  } else {
                    wsState = 'closed-before-send';
                    console.log(`[bridge] WS closed (state=${clientWs.readyState}) dropping event #${eventCount} type=${lastEventType}`);
                  }
                }
              }
            };

            while (true) {
              const { done, value } = await reader.read();
              if (done) break;

              buffer += decoder.decode(value, { stream: true });
              const lines = buffer.split('\n');
              buffer = lines.pop() || '';

              for (const line of lines) {
                processLine(line);
              }
            }

            if (buffer.trim()) {
              processLine(buffer);
            }

            console.log(`[bridge] SSE stream ended: ${eventCount} events, lastType=${lastEventType}, wsState=${wsState}`);
          } catch (err) {
            if (err.name !== 'AbortError') {
              console.error(`[bridge] Error: ${err.message}`);
              if (clientWs.readyState === 1) {
                clientWs.send(JSON.stringify({ error: err.message }));
              }
            } else {
              console.log(`[bridge] Fetch aborted (client disconnected), ${eventCount} events sent, lastType=${lastEventType}`);
            }
          } finally {
            clearInterval(pingInterval);
            if (clientWs.readyState === 1) {
              clientWs.close();
            }
          }
        });

        clientWs.on('close', () => {
          console.log('[bridge] Client WS closed');
          clearInterval(pingInterval);
          if (abortController) abortController.abort();
        });

        clientWs.on('error', (err) => {
          console.error('[bridge] Client WS error:', err.message);
          clearInterval(pingInterval);
          if (abortController) abortController.abort();
        });
      });
    }
  });

  server.listen(port, hostname, () => {
    console.log(`> Custom server ready on http://${hostname}:${port}`);
  });
});
