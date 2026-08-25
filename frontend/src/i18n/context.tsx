import React, { createContext, useContext, useState, useEffect } from "react";
import { en } from "./locales/en";
import { vi } from "./locales/vi";

type Language = "en" | "vi";
type Translations = Record<string, any>;

interface I18nContextType {
  language: Language;
  setLanguage: (lang: Language) => void;
  t: (keyPath: string, params?: Record<string, string | number>) => string;
}

const translations: Record<Language, any> = { en, vi };

const I18nContext = createContext<I18nContextType | undefined>(undefined);

export const I18nProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [language, setLanguageState] = useState<Language>(() => {
    const saved = localStorage.getItem("ridepulse.lang");
    return (saved === "vi" || saved === "en") ? saved : "en";
  });

  const setLanguage = (lang: Language) => {
    setLanguageState(lang);
    localStorage.setItem("ridepulse.lang", lang);
  };

  const t = (keyPath: string, params?: Record<string, string | number>): string => {
    const keys = keyPath.split(".");
    let current: any = translations[language] || translations.en;
    
    for (const key of keys) {
      if (current && typeof current === "object" && key in current) {
        current = current[key];
      } else {
        // Fallback to EN if missing in current language
        let fallback: any = translations.en;
        for (const k of keys) {
          if (fallback && typeof fallback === "object" && k in fallback) {
            fallback = fallback[k];
          } else {
            return keyPath;
          }
        }
        current = fallback;
        break;
      }
    }

    if (typeof current !== "string") return keyPath;

    if (params) {
      return Object.entries(params).reduce((acc, [k, v]) => {
        return acc.replace(new RegExp(`{{${k}}}`, "g"), String(v));
      }, current);
    }

    return current;
  };

  return (
    <I18nContext.Provider value={{ language, setLanguage, t }}>
      {children}
    </I18nContext.Provider>
  );
};

export const useI18n = () => {
  const context = useContext(I18nContext);
  if (!context) {
    throw new Error("useI18n must be used within an I18nProvider");
  }
  return context;
};
