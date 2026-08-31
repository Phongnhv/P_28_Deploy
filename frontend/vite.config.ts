import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // Đường lui khi VITE_API_BASE_URL không được đặt: chuyển tiếp /api sang
    // backend chạy trong Docker. Trước đây target là chuỗi giữ chỗ
    // "http://[IP_ADDRESS]" chưa ai thay, nên mọi lời gọi đi qua proxy đều hỏng.
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
});
