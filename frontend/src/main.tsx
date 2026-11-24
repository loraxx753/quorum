import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./index.css";
import { ConversationPage } from "./pages/ConversationPage";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ConversationPage />
  </StrictMode>,
);
