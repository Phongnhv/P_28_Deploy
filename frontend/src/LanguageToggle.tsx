import { useI18n } from "./i18n/context";

export default function LanguageToggle() {
  const { language, setLanguage } = useI18n();

  return (
    <div className="language-toggle" title="Switch Language / Đổi ngôn ngữ">
      <button
        type="button"
        className={`lang-btn ${language === "en" ? "active" : ""}`}
        onClick={() => setLanguage("en")}
        aria-label="English"
      >
        EN
      </button>
      <span className="lang-divider">|</span>
      <button
        type="button"
        className={`lang-btn ${language === "vi" ? "active" : ""}`}
        onClick={() => setLanguage("vi")}
        aria-label="Tiếng Việt"
      >
        VI
      </button>
    </div>
  );
}
