import type { SVGProps } from "react";

type IconProps = SVGProps<SVGSVGElement>;
type Path = { d?: string; points?: string; cx?: string; cy?: string; r?: string; x1?: string; x2?: string; y1?: string; y2?: string };

function icon(paths: Path[]) {
  return function AnalysisIcon(props: IconProps) {
    return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" {...props}>
      {paths.map((path, index) => path.points
        ? <polyline key={index} points={path.points} />
        : path.cx
          ? <circle key={index} cx={path.cx} cy={path.cy} r={path.r} />
          : path.x1
            ? <line key={index} x1={path.x1} y1={path.y1} x2={path.x2} y2={path.y2} />
            : <path key={index} d={path.d} />)}
    </svg>;
  };
}

export const Activity = icon([{ points: "22 12 18 12 15 21 9 3 6 12 2 12" }]);
export const AlertTriangle = icon([{ d: "M10.3 2.9 1.8 17a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 2.9a2 2 0 0 0-3.4 0Z" }, { x1: "12", y1: "9", x2: "12", y2: "13" }, { x1: "12", y1: "17", x2: "12.01", y2: "17" }]);
export const ArrowLeft = icon([{ d: "m15 18-6-6 6-6" }, { x1: "9", y1: "12", x2: "21", y2: "12" }]);
export const CheckCircle2 = icon([{ cx: "12", cy: "12", r: "10" }, { d: "m9 12 2 2 4-4" }]);
export const ChevronDown = icon([{ points: "6 9 12 15 18 9" }]);
export const ChevronUp = icon([{ points: "18 15 12 9 6 15" }]);
export const Clock3 = icon([{ cx: "12", cy: "12", r: "10" }, { d: "M12 6v6h4" }]);
export const Copy = icon([{ d: "M8 8h11a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H10a2 2 0 0 1-2-2Z" }, { d: "M16 8V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h3" }]);
export const Database = icon([{ d: "M4 5c0 1.1 3.6 2 8 2s8-.9 8-2-3.6-2-8-2-8 .9-8 2Z" }, { d: "M4 5v6c0 1.1 3.6 2 8 2s8-.9 8-2V5" }, { d: "M4 11v6c0 1.1 3.6 2 8 2s8-.9 8-2v-6" }]);
export const Download = icon([{ d: "M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" }, { points: "7 10 12 15 17 10" }, { x1: "12", y1: "15", x2: "12", y2: "3" }]);
export const FileText = icon([{ d: "M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z" }, { points: "14 2 14 8 20 8" }, { x1: "8", y1: "13", x2: "16", y2: "13" }, { x1: "8", y1: "17", x2: "16", y2: "17" }]);
export const Search = icon([{ cx: "11", cy: "11", r: "8" }, { x1: "21", y1: "21", x2: "16.65", y2: "16.65" }]);
export const ShieldAlert = icon([{ d: "M20 13c0 5-3.5 7.5-8 9-4.5-1.5-8-4-8-9V5l8-3 8 3Z" }, { x1: "12", y1: "8", x2: "12", y2: "12" }, { x1: "12", y1: "16", x2: "12.01", y2: "16" }]);
export const XCircle = icon([{ cx: "12", cy: "12", r: "10" }, { x1: "15", y1: "9", x2: "9", y2: "15" }, { x1: "9", y1: "9", x2: "15", y2: "15" }]);
