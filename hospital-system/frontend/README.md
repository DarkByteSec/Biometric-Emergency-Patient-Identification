# Hospital frontend

React + TypeScript + Vite client for the same-origin Flask API. It includes login, webcam permission/capture, manual National ID lookup, explicit candidate confirmation/rejection, safe Central outcome messages, qualified patient warnings, and redacted scan history.

```bash
npm install
npm run dev
npm run build
```

The browser never receives database credentials or Central machine credentials and never calls Central directly. Use synthetic or specifically authorized test data only.
