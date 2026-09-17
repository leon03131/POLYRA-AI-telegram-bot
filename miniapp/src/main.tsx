import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import { initTelegramWebApp } from "./telegram/webapp";
import "./styles.css";

// ready() + expand() + тема — как можно раньше, до рендера.
initTelegramWebApp();

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
