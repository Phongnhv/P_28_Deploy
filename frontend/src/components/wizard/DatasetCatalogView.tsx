import type { Dataset, DatasetProfile } from "../../types";

/**
 * Every registered dataset with the profile numbers behind each one.
 *
 * The step-1 profile panel is deliberately scoped to the selected dataset, so
 * this is where the cross-dataset comparison lives instead — reachable from
 * "Dataset catalog" without leaving the step.
 */
export function DatasetCatalogView({
  datasets,
  datasetProfiles,
  selectedId,
  language,
  onSelectDataset,
}: {
  datasets: Dataset[];
  datasetProfiles: Record<string, DatasetProfile>;
  selectedId?: string;
  language: "en" | "vi";
  onSelectDataset: (datasetId: string) => void;
}) {
  const vi = language === "vi";

  function completeness(dataset: Dataset): number | null {
    const value = datasetProfiles[dataset.id]?.completeness_score;
    return typeof value === "number" ? value : null;
  }

  if (!datasets.length) {
    return (
      <div className="empty-state">
        <h2>{vi ? "Chưa có bộ dữ liệu nào." : "No datasets registered."}</h2>
        <p className="muted">{vi ? "Tải một tệp lên ở phần 1." : "Upload a file in part 1."}</p>
      </div>
    );
  }

  return (
    <div className="catalog-view">
      <p className="catalog-view-caption">
        {vi
          ? `${datasets.length} bộ dữ liệu đã đăng ký. Bấm một dòng để chọn làm bộ đang xử lý.`
          : `${datasets.length} registered datasets. Click a row to make it the active one.`}
      </p>
      <div className="catalog-view-scroll">
        <table className="catalog-view-table">
          <thead>
            <tr>
              <th>{vi ? "Tên" : "Name"}</th>
              <th>{vi ? "Trạng thái" : "Status"}</th>
              <th className="numeric">{vi ? "Số dòng" : "Rows"}</th>
              <th className="numeric">{vi ? "Số cột" : "Columns"}</th>
              <th className="numeric">{vi ? "Đầy đủ" : "Completeness"}</th>
              <th>{vi ? "Nguồn" : "Source"}</th>
              <th>{vi ? "Phiên bản" : "Version"}</th>
            </tr>
          </thead>
          <tbody>
            {datasets.map((item) => {
              const score = completeness(item);
              const profile = datasetProfiles[item.id];
              return (
                <tr
                  key={item.id}
                  className={item.id === selectedId ? "active" : ""}
                  onClick={() => onSelectDataset(item.id)}
                >
                  <td>
                    <strong>{item.name}</strong>
                    <span className="catalog-view-sub">{item.description}</span>
                  </td>
                  <td>
                    <span className={`prep-status ${item.id === selectedId ? "selected" : ""}`}>
                      <span className="prep-status-dot" />
                      {item.status.replaceAll("_", " ")}
                    </span>
                  </td>
                  <td className="numeric">{item.row_count.toLocaleString()}</td>
                  <td className="numeric">{profile?.columns?.length ?? "—"}</td>
                  <td className="numeric">{score === null ? "—" : `${score.toFixed(1)}%`}</td>
                  <td className="catalog-view-source" title={item.source_label}>{item.source_label}</td>
                  <td><code>{item.manifest_version}</code></td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
