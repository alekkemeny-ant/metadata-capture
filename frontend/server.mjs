import { createServer } from 'http';
import next from 'next';
import { WebSocketServer, WebSocket } from 'ws';

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
        const backendWs = new WebSocket('ws://localhost:8001/ws/chat');
        let pendingSends = 0;

        const pingInterval = setInterval(() => {
          if (clientWs.readyState === WebSocket.OPEN) {
            clientWs.ping();
          }
        }, 20000);

        const closeClient = () => {
          const doClose = () => {
            clearInterval(pingInterval);
            if (clientWs.readyState === WebSocket.OPEN) {
              clientWs.close();
            }
          };
          if (pendingSends > 0) {
            const check = setInterval(() => {
              if (pendingSends <= 0) {
                clearInterval(check);
                setTimeout(doClose, 50);
              }
            }, 10);
            setTimeout(() => { clearInterval(check); doClose(); }, 2000);
          } else {
            setTimeout(doClose, 50);
          }
        };

        backendWs.on('open', () => {
          clientWs.on('message', (data) => {
            if (backendWs.readyState === WebSocket.OPEN) {
              backendWs.send(data.toString());
            }
          });

          backendWs.on('message', (data) => {
            if (clientWs.readyState === WebSocket.OPEN) {
              pendingSends++;
              clientWs.send(data.toString(), (err) => {
                pendingSends--;
                if (err) {
                  console.error('Failed to forward WS message to client:', err.message);
                }
              });
            }
          });
        });

        backendWs.on('close', () => {
          closeClient();
        });

        backendWs.on('error', () => {
          clearInterval(pingInterval);
          if (clientWs.readyState === WebSocket.OPEN) {
            clientWs.close();
          }
        });

        clientWs.on('close', () => {
          clearInterval(pingInterval);
          if (backendWs.readyState === WebSocket.OPEN || backendWs.readyState === WebSocket.CONNECTING) {
            backendWs.terminate();
          }
        });

        clientWs.on('error', () => {
          clearInterval(pingInterval);
          if (backendWs.readyState === WebSocket.OPEN || backendWs.readyState === WebSocket.CONNECTING) {
            backendWs.terminate();
          }
        });
      });
    }
  });

  server.listen(port, hostname, () => {
    console.log(`> Custom server ready on http://${hostname}:${port}`);
  });
});
