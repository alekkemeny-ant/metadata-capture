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
    if (req.url === '/ws/chat') {
      wss.handleUpgrade(req, socket, head, (clientWs) => {
        const backendWs = new WebSocket('ws://localhost:8001/ws/chat');

        backendWs.on('open', () => {
          clientWs.on('message', (data) => {
            if (backendWs.readyState === WebSocket.OPEN) {
              backendWs.send(data);
            }
          });

          backendWs.on('message', (data) => {
            if (clientWs.readyState === WebSocket.OPEN) {
              clientWs.send(data);
            }
          });
        });

        backendWs.on('close', () => {
          if (clientWs.readyState === WebSocket.OPEN) {
            clientWs.close();
          }
        });

        backendWs.on('error', () => {
          if (clientWs.readyState === WebSocket.OPEN) {
            clientWs.close();
          }
        });

        clientWs.on('close', () => {
          if (backendWs.readyState === WebSocket.OPEN) {
            backendWs.close();
          }
        });

        clientWs.on('error', () => {
          if (backendWs.readyState === WebSocket.OPEN) {
            backendWs.close();
          }
        });
      });
    } else {
      socket.destroy();
    }
  });

  server.listen(port, hostname, () => {
    console.log(`> Custom server ready on http://${hostname}:${port}`);
  });
});
