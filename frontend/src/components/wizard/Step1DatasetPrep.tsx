import React, { useState } from "react";
import { useI18n } from "../../i18n/context";

interface Dataset {
  id: string;
  name: string;
  rows?: number;
  columns_count?: number;
  source_type?: string;
  updated_at?: string;
}

interface Step1DatasetPrepProps {
  datasets: Dataset[];
  selectedDatasetId: string | null;
  onSelectDataset: (id: string) => void;
  onUploadDataset: (file: File) => Promise<void>;
  onTriggerUnderstand: (id: string) => Promise<void>;
  onNext: () => void;
  loading: boolean;
}

export const Step1DatasetPrep: React.FC<Step1DatasetPrepProps> = ({
  datasets,
  selectedDatasetId,
  onSelectDataset,
  onUploadDataset,
  onTriggerUnderstand,
  onNext,
  loading,
}) => {
  const { t } = useI18n();
  const [file, setFile] = useState<File | null>(null);
  const [isUploading, setIsUploading] = useState(false);

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files[0]) {
      setFile(e.target.files[0]);
    }
  };

  const handleUpload = async () => {
    if (!file) return;
    setIsUploading(true);
    try {
      await onUploadDataset(file);
      setFile(null);
    } finally {
      setIsUploading(false);
    }
  };

  const selectedDataset = datasets.find((d) => d.id === selectedDatasetId);

  return (
    <div className="wizard-step-container" style={{ padding: "24px", maxWidth: "1200px", margin: "0 auto" }}>
      <div className="step-header" style={{ marginBottom: "24px" }}>
        <h2 style={{ fontSize: "20px", fontWeight: 600, color: "var(--color-text-main, #1e293b)" }}>
          {t("wizard.step1Title")}
        </h2>
        <p style={{ color: "var(--color-text-muted, #64748b)", fontSize: "14px", marginTop: "4px" }}>
          {t("wizard.step1Desc")}
        </p>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "2fr 1fr", gap: "24px" }}>
        {/* Left Column: Dataset Catalog */}
        <div className="card" style={{ background: "var(--color-bg-card, #ffffff)", borderRadius: "12px", border: "1px solid var(--color-border, #e2e8f0)", padding: "20px" }}>
          <h3 style={{ fontSize: "16px", fontWeight: 600, marginBottom: "16px" }}>{t("datasets.title")}</h3>
          {datasets.length === 0 ? (
            <p style={{ color: "#64748b" }}>{t("datasets.noDatasets")}</p>
          ) : (
            <div style={{ display: "grid", gap: "12px" }}>
              {datasets.map((ds) => {
                const isSelected = ds.id === selectedDatasetId;
                return (
                  <div
                    key={ds.id}
                    onClick={() => onSelectDataset(ds.id)}
                    style={{
                      padding: "16px",
                      borderRadius: "8px",
                      border: isSelected ? "2px solid #2563eb" : "1px solid #e2e8f0",
                      background: isSelected ? "#eff6ff" : "#f8fafc",
                      cursor: "pointer",
                      display: "flex",
                      justifyContent: "space-between",
                      alignItems: "center",
                      transition: "all 0.2s ease",
                    }}
                  >
                    <div>
                      <div style={{ fontWeight: 600, color: isSelected ? "#1e40af" : "#1e293b" }}>{ds.name}</div>
                      <div style={{ fontSize: "13px", color: "#64748b", marginTop: "4px" }}>
                        {t("datasets.rows")}: {ds.rows?.toLocaleString() ?? "N/A"} | {t("datasets.source")}: {ds.source_type ?? "CSV/Parquet"}
                      </div>
                    </div>
                    {isSelected && (
                      <span style={{ fontSize: "12px", background: "#2563eb", color: "#fff", padding: "4px 8px", borderRadius: "12px", fontWeight: 500 }}>
                        Active
                      </span>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>

        {/* Right Column: Custom Import & Actions */}
        <div style={{ display: "flex", flexDirection: "column", gap: "20px" }}>
          <div className="card" style={{ background: "var(--color-bg-card, #ffffff)", borderRadius: "12px", border: "1px solid var(--color-border, #e2e8f0)", padding: "20px" }}>
            <h3 style={{ fontSize: "16px", fontWeight: 600, marginBottom: "12px" }}>{t("datasets.import")}</h3>
            <input type="file" accept=".csv,.parquet" onChange={handleFileChange} style={{ fontSize: "14px", marginBottom: "12px", width: "100%" }} />
            <button
              onClick={handleUpload}
              disabled={!file || isUploading}
              style={{
                width: "100%",
                padding: "10px",
                background: "#2563eb",
                color: "#fff",
                border: "none",
                borderRadius: "6px",
                fontWeight: 500,
                cursor: !file || isUploading ? "not-allowed" : "pointer",
                opacity: !file || isUploading ? 0.6 : 1,
              }}
            >
              {isUploading ? "Uploading…" : t("datasets.uploadBtn")}
            </button>
          </div>

          {selectedDataset && (
            <div className="card" style={{ background: "#f0fdf4", borderRadius: "12px", border: "1px solid #bbf7d0", padding: "20px" }}>
              <h4 style={{ fontSize: "15px", fontWeight: 600, color: "#166534", marginBottom: "8px" }}>Selected Dataset</h4>
              <p style={{ fontSize: "13px", color: "#15803d", marginBottom: "16px" }}>{selectedDataset.name}</p>
              <button
                onClick={() => onTriggerUnderstand(selectedDataset.id)}
                disabled={loading}
                style={{
                  width: "100%",
                  padding: "10px",
                  background: "#16a34a",
                  color: "#fff",
                  border: "none",
                  borderRadius: "6px",
                  fontWeight: 500,
                  cursor: loading ? "not-allowed" : "pointer",
                  marginBottom: "12px",
                }}
              >
                {loading ? t("datasets.profiling") : t("datasets.understandDataset")}
              </button>
              <button
                onClick={onNext}
                style={{
                  width: "100%",
                  padding: "10px",
                  background: "#1e293b",
                  color: "#fff",
                  border: "none",
                  borderRadius: "6px",
                  fontWeight: 500,
                  cursor: "pointer",
                }}
              >
                {t("wizard.next")}
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
