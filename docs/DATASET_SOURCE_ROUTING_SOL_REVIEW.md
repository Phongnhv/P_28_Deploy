# Phản biện source routing và kế hoạch kiểm thử Luna

Ngày: 2026-09-03. Repo HEAD: a3441c607b610e347d307fe428c2736f408802b2.

Đã đọc report, diagnosis, toàn bộ diff tracked hiện tại, resolver mới và các caller liên quan. Đây là review phần sửa local và các lỗ hổng còn sót, không phải xác nhận lỗi đã hết. Không sửa production code, commit/push/deploy, gọi Luna hoặc truy cập/sửa cloud trong lượt review này. Chỉ thêm tài liệu này và probe trong scratch.

## Kết luận về nguyên nhân

- Routing sai là một lỗi thực có bằng chứng code và kiểm thử local. Dataset có file/profile vẫn có thể bị đọc qua đường nguồn khác. Không được diễn giải thành “dataset upload rỗng”.
- Theo A/B người dùng cung cấp, sau khi import 10.000 dòng, số candidates tăng 4 → 36 và một lượt Luna thành công sau retry. Điều này hỗ trợ nhận định thiếu evidence do đường đọc là yếu tố đóng góp. Hai lượt chưa chứng minh routing là nguyên nhân duy nhất: số candidates, nội dung prompt và quá trình retry cũng thay đổi; đầu ra LLM có biến thiên. Review này chưa đối chiếu lại raw provider trace của hai run 057916d390e540af86e87103257d449f và 7106e7f661ec444abc2499831cc0d40f.
- Wizard B đã nhận profile 12 dòng, 6 cột ở Graph 1A theo diagnosis. Không có căn cứ quy lỗi của lượt đó cho việc Graph 1A đọc source_rows. Graph 1B–3 của lượt cloud ấy vẫn chưa được chứng minh.
- HTTP 200 kèm output bị CandidateTableRuleDraft/RuleConfidence reject là thất bại ở structured-output/validation sau khi có phản hồi; không đủ căn cứ gọi đó là “API key không trả output”. Phải giữ riêng lỗi transport, deadline, vòng tool, parser và validator.
- Profile COMPLETED và nguồn vật lý còn truy cập được là hai điều kiện khác nhau. Một workflow dùng snapshot đã hoàn tất có thể hiểu dữ liệu được, rồi Graph 2 mới báo mất file. Locator local trên cloud là bằng chứng nguồn không bền vững giữa instance, không xác định tác nhân lịch sử đã làm file mất.

## Các lỗ hổng còn sót

Đây phần lớn là các đường cũ chưa được binding mới bao phủ, không khẳng định tất cả là regression mới do diff.

| Ưu tiên | Lỗ hổng | Bằng chứng và phạm vi |
|---|---|---|
| P1 | Tool của proposer vẫn chọn latest | src/agents/tools/rule_proposer_tools.py:56 gọi materializer không truyền version; :79–95 tự lấy version/profile mới nhất. Run proposal đã pin v1 vẫn có thể dry-run/sample trên v2; deep stats cũng không nhận profile ID. Probe pin B/v1 có 2 dòng, thêm B/v2 có 4 dòng, rồi loader thực trả v2 với min(amount)=100. Ảnh hưởng đường DeepAgent có tools, gồm CLI; Graph 1B dashboard có dashboard_candidate_mode và không cấp bộ data tools này. |
| P1 | Graph 3 detector vẫn dùng legacy profile | src/services/anomaly_service.py:341–348 đọc ProfileModel theo dataset, không nhận binding/profile của run. Probe B/v1 đã pin profile 2 dòng, có legacy profile 999 dòng: detect_anomalies sinh VOLUME observed_value=999. Nếu chỉ có profile_runs, nhánh VOLUME bị bỏ qua. Wrapper investigation tools không sửa được đầu vào detector này. |
| P1 | Graph1 Studio → analysis bỏ qua scope của tools | src/services/analysis_workflow.py:599–608 truyền metadata.analysis_run_id; src/agents/tools/anomaly_investigation_tools.py:368–370 chỉ bật scope khi có metadata.workflow_run_id. Probe dùng đúng shape state này trả B/v2 7 dòng dù state pin B/v1 2 dòng, và cho gọi profile A. Fixture v2 ở probe này là metadata tổng hợp để cô lập resolver, không phải chứng minh profile v2 đã được source-verified. Thêm nữa, AnomalyGraphState không khai báo profile_run_id, nên top-level field này không phải kênh state được giữ qua graph. |
| P1 | CLI Graph 2 làm rơi binding của proposal | src/agents/graph.py:757–762 chỉ đưa dataset, test_run, rule_run, approved_rules vào execution graph. Probe chặn tại biên graph xác nhận không có version/profile/binding dù B là versioned. Nhánh versioned trong test_generator_node.py:649 và test_runner_node.py:648 sẽ không được chọn. CLI all còn gọi execution theo active rules mà không mang binding của proposal trước đó. Probe chứng minh state thiếu, chưa thực thi SQL sai nguồn. |
| P2 | Materializer Graph 2 bỏ qua source_ref đã pin | src/services/job_runner.py:133–143 lấy SOURCE_DATASET đầu tiên theo dataset/version, không lọc metadata.source_artifact_id hay workspace như resolver. Schema không có unique constraint cho một SOURCE_DATASET/version. Probe tạo artifact cũ trỏ file thiếu và artifact hiện hành hợp lệ cùng checksum: fresh profile và binding đúng artifact hiện hành, nhưng materializer chọn artifact cũ rồi lỗi missing. Checksum vẫn chặn nội dung khác; kết quả này chứng minh chọn sai artifact/false missing, không chứng minh đọc bytes sai vượt checksum. |
| P1 | Standalone anomaly chưa xác thực execution thuộc dataset | src/agents/graph.py:837–859 còn chọn run bất kỳ khi không tìm được dataset; :862–872 chỉ kiểm tra khi stream_id resolve ra WorkflowRunModel. Probe truyền dataset B và execution của A được đưa thẳng vào graph. stream_id không tồn tại cũng không bị fail-closed. Đây xác nhận điểm đã nêu trong handoff, đồng thời cho thấy truyền execution ID tường minh chưa đủ an toàn. |

Hướng sửa: mang một binding server-owned xuyên entrypoint và tool factory; cho tool nhận resource ID từ scope thay vì tự chọn latest; truyền snapshot vào detector; bắt CLI execution dùng đúng version/profile/ruleset của proposal; materializer resolve đúng artifact ID; anomaly entrypoint phải kiểm execution/dataset trước khi tạo graph. Giữ đủ node/tool, không cần đổi model, timeout hay nới validator để sửa các lỗi này.

## Những giới hạn cần ghi chính xác hơn trong report

1. “Pin artifact xuyên graph” hiện là lineage của output, chưa bảo đảm evidence bên trong detector/tool. Contract test gọi _add_artifact bốn lần không chạy bốn graph. Các probe trên cho thấy output có thể mang binding đúng trong khi node khác đọc nguồn khác.
2. Resolver hiện so metadata/checksum và row_count của snapshot, không đọc file khi resolve. Việc đọc và verify file thật nằm ở fresh profiler/materializer. Không nên mô tả mọi lần resolve là đã xác minh byte nguồn.
3. workflow_binding không đối chiếu row_count/source_kind đã lưu với giá trị resolved, và resolve_source_binding không đối chiếu schema_json của profile với schema_hash của version. Đây là khoảng trống kiểm tra integrity thêm; chưa có bằng chứng dữ liệu cloud đã bị lỗi theo cách này.
4. API được phép dùng completed profile của version đã chọn khi không yêu cầu fresh_profile là một chính sách riêng. Không tự xem reuse đó là sai; cần kiểm đúng version/profile và ghi rõ thời điểm source được verify gần nhất.
5. Lịch sử dùng cho drift có thể cố ý đi qua nhiều version. Cần phân biệt profile hiện tại phải pin đúng với baseline lịch sử cần chính sách cohort/thời gian; không máy móc ép mọi history về cùng version.

## Vì sao A/B CLI chưa dự đoán được wizard

- Graph 1B wizard đặt metadata.max_retries=0 ở dashboard_agent_workflow.py:760. _propose_for_table lặp max_retries+1; CLI mặc định rule_proposer_max_retries=2. Đây là số attempt vòng ngoài, không phải tổng số model calls hay sửa lỗi structured-output bên trong DeepAgent.
- Dashboard digest bật dashboard_candidate_mode. DeepAgent dùng prompt/dashboard checklist và không cấp data investigation tools; CLI dùng đường khác với tools. So cùng tên model không làm hai entrypoint thành cùng thí nghiệm.
- Khi Graph 1B lỗi, workflow còn có deterministic-policy fallback ở rule_proposer_workflow.py:805–814. Node proposer có heuristic promotion riêng. Cả hai đều khác legacy one-shot và report fallback.
- RuleConfidence kiểm abs(overall - mean(ba thành phần)) <= 0.25 tại rule_schemas.py:159. Đây là quan hệ liên trường, không thể suy ra chỉ từ các giới hạn 0–1 của từng field trong JSON Schema. Skill proposer chỉ nói overall phải “reasonably consistent”, chưa ghi ngưỡng số. Đây là ứng viên prompt/contract mismatch cần kiểm bằng prompt thực gửi đi, chưa phải kết luận provider lỗi.
- last_exc được thêm vào prompt legacy, nhưng vòng retry DeepAgent gọi lại helper mà không truyền last_exc. Không được mặc định outer retry là retry có phản hồi lỗi; middleware nội bộ có thể có cơ chế khác cần trace riêng.

## Kiểm thử real Luna tối thiểu được đề xuất

Mục tiêu đầu tiên là phân loại điểm hỏng, không đo tỷ lệ lỗi của model từ một lượt.

### 0. Chuẩn bị local, chưa gọi LLM

Dùng DB và source directory riêng, tắt external tracing; cùng model/provider, AGENT_MODE=graph, RULE_PROPOSER_MODE=deepagent, legacy fallback tắt. Ghi cấu hình đã resolve, kể cả attempt/tool limits và timeout hiện hành; không tăng chúng.

Dùng CSV tổng hợp B/v1 12 dòng, 6 cột với một số null, duplicate và giá trị ngoài miền biết trước. Tính SHA-256, row count, schema, null count và kết quả các luật được duyệt bằng phép tính độc lập từ CSV. source_rows có dữ liệu mồi A khác schema; B không được import vào source_rows.

Chạy lại các contract sau khi sửa các lỗ hổng trên. Source thiếu/checksum sai phải dừng trước model, không fallback nguồn khác. Giữ bản input digest, semantic contract, candidate checklist và validator schema để có thể replay.

### 1. Một workflow thật qua đúng entrypoint wizard/API

Tạo fresh profile B/v1, chạy Graph 1A, xác nhận contract, Graph 1B, duyệt/publish luật trong DB test, Graph 2 rồi Graph 3 với Luna thật tại các node LLM.

Sau Graph 1A, thêm B/v2 local có checksum và số liệu khác để kiểm chống trôi version, rồi gọi API các stage còn lại với workflow/version v1 tường minh. Không dùng UI tự chọn latest cho ca này; UI hiện có guard từ chối khi dataset đang chọn mang version khác workflow.

Ở từng biên graph và từng data tool ghi: entrypoint, workflow/analysis/proposal/execution IDs, dataset/version/profile/source_ref/checksum; digest/candidate hash; số dòng/cột; tool args và nguồn thực đọc. Graph 2 phải khớp counts độc lập; Graph 3 phải nêu đúng profile hiện tại và có evidence_refs kiểm tra được. Binding đúng trên report nhưng tool/detector đọc khác vẫn là fail.

Nếu Graph 1B fail, giữ output/error/fallback và chuyển sang bước 2. Không gọi đó là hoàn thành real-LLM end-to-end; có thể tiếp tục kiểm Graph 2/3 bằng luật duyệt local nhưng phải đánh dấu đường fallback rõ ràng.

### 2. Replay tối thiểu để tách prompt/validator với provider

Lấy đúng payload Graph 1B đã đóng băng ở bước 1, gọi thêm một lần qua cùng DeepAgent/provider/validator và cùng retry policy. Không chạy lại profiler/Graph 1A để tránh thay đổi đầu vào. Nếu hai lần khác kết quả, đó là bằng chứng biến thiên, chưa đủ ước lượng độ ổn định.

Với mỗi attempt lưu local phản hồi trước parser, finish/stop reason nếu có, request ID, elapsed time, model/tool call count, lỗi đầy đủ của validator (field path và values liên quan), output sau bind. Tách provider SDK retry, structured-output retry, outer retry và toàn graph deadline.

| Quan sát | Kết luận được phép |
|---|---|
| Sai binding/hash hoặc tool trả số liệu v2/A | Routing còn lỗi; chưa dùng lượt này để phán xét chất lượng Luna trên evidence đúng. |
| Evidence đúng, phản hồi đầy đủ, confidence hoặc candidate/evidence IDs bị reject | Lỗi output so với contract; phân tích prompt/schema/binder. Không gọi là mất phản hồi provider. |
| Không nhận được phản hồi đầy đủ, 429/5xx/network/deadline | Điều tra transport/provider hoặc orchestration tại mốc cụ thể; không suy ra từ HTTP job 200. |
| Có nhiều model/tool turns trước deadline | Kiểm vòng agent/tool latency và ngân sách thời gian; không tự quy cho provider không trả output. |
| Graph 1B hợp lệ, Graph 3 report timeout | Lỗi riêng ở Graph 3; routing/Graph 1B pass không giải quyết timeout này. |

Chỉ khi tái hiện confidence mismatch trên payload đúng mới thêm một biến thể prompt nêu chính xác công thức <=0.25, giữ nguyên validator/model/timeout. So với prompt gốc trên cùng payload; một lần pass chỉ cho tín hiệu chẩn đoán, chưa chứng minh sửa triệt để.

Như vậy baseline tối thiểu là một workflow thật và một lần replay Graph 1B; chỉ thêm lần gọi khi cần phân biệt giả thuyết cụ thể. Để chứng minh riêng CLI đã sửa, cần thêm một lượt CLI có binding tường minh sau khi khắc phục các lỗ hổng CLI/tools; kết quả wizard không thay thế lượt đó.

## Kiểm chứng đã thực hiện trong review này

Probe: scratch/test_source_binding_review.py. Sáu probe đều tái hiện hành vi sai mong đợi: **6 passed in 1.47s**. Đây là test chẩn đoán với assertion mô tả lỗi hiện tại; “passed” nghĩa là tái hiện được lỗi, không phải regression gate xác nhận bản sửa. Graph/LLM được thay bằng AsyncMock ở hai probe entrypoint; detector, source materializer và profile tools chạy code thật trên SQLite test.

Lệnh đã chạy:

```powershell
$env:LANGSMITH_TRACING='false'
$env:LANGCHAIN_TRACING_V2='false'
.venv-e2e/Scripts/python.exe -m pytest -p tests.conftest scratch/test_source_binding_review.py -q --basetemp=scratch/review-probes-second-20260903
```

Khi chạy lại, chọn basetemp mới. git diff --check pass. Không chạy lại toàn bộ bộ 102 test hay full suite, không browser E2E hoặc real Luna trong review này. Sáu probe bổ sung không thay thế các kiểm thử đó.
