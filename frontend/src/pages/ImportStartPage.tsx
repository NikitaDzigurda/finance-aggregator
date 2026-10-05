import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  FileCheck2,
  FileSpreadsheet,
  LockKeyhole,
  UploadCloud,
} from "lucide-react";
import { useMemo, useState, type FormEvent } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { ApiFailure, api, unwrap, uploadImport } from "../api/client";
import {
  Button,
  ImportStepper,
  PageHeader,
  QueryState,
} from "../components/ui";
import { userFacingName } from "../lib/format";

const providerLabels: Record<string, { title: string; description: string }> = {
  tbank_broker_xlsx: {
    title: "Т‑Инвестиции",
    description: "Официальный брокерский XLSX",
  },
  alfa_broker_xml_import: {
    title: "Альфа‑Инвестиции",
    description: "XML «для импорта»",
  },
  bybit_spot_csv_bundle: {
    title: "Байбит · спотовый счёт",
    description: "Комплект из четырёх CSV",
  },
};

const bybitDocuments = [
  { label: "История спотовых сделок", marker: "spottradehistory" },
  { label: "Изменения активов единого счёта", marker: "assetchangedetails_uta" },
  { label: "Изменения активов счёта финансирования", marker: "assetchangedetails_fund" },
  { label: "История выводов и пополнений", marker: "withdrawdeposithistory" },
] as const;

function formFailure(error: Error | null): string | null {
  if (!error) return null;
  if (!(error instanceof ApiFailure)) return error.message;
  const messages: Record<string, string> = {
    import_account_type_mismatch:
      "Выберите брокерский счёт для отчёта брокера или счёт криптобиржи для Байбит.",
    import_file_count_invalid:
      "Для Байбит нужны четыре CSV одного экспорта, для брокера — один отчёт.",
    import_extension_mismatch:
      "Формат выбранного файла не соответствует выбранному источнику.",
    import_file_signature_invalid:
      "Содержимое файла не соответствует заявленному формату.",
  };
  return (
    messages[error.code] ??
    "Не удалось загрузить файл. Проверьте его и повторите."
  );
}

export function ImportStartPage() {
  const { portfolioId = "" } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const accounts = useQuery({
    queryKey: ["accounts", portfolioId],
    queryFn: async () =>
      unwrap(
        await api.GET("/api/v1/portfolios/{portfolio_id}/accounts", {
          params: {
            path: { portfolio_id: portfolioId },
            query: { limit: 100 },
          },
        }),
      ),
  });
  const formats = useQuery({
    queryKey: ["import-formats"],
    queryFn: async () => unwrap(await api.GET("/api/v1/import-formats")),
  });
  const [provider, setProvider] = useState("");
  const [accountId, setAccountId] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [showAccountForm, setShowAccountForm] = useState(false);
  const [newAccountName, setNewAccountName] = useState("Новый брокер");
  const [institutionName, setInstitutionName] = useState("");
  const publicFormats = useMemo(
    () =>
      formats.data?.items.filter(
        (item) => item.format_id !== "universal_broker",
      ) ?? [],
    [formats.data],
  );
  const selectedProvider = provider || publicFormats[0]?.format_id || "";
  const descriptor = useMemo(
    () =>
      formats.data?.items.find((item) => item.format_id === selectedProvider),
    [formats.data, selectedProvider],
  );
  const expectedAccountType =
    selectedProvider === "bybit_spot_csv_bundle" ? "cex" : "broker";
  const compatibleAccounts = useMemo(
    () =>
      accounts.data?.items.filter(
        (item) => item.account_type === expectedAccountType,
      ) ?? [],
    [accounts.data, expectedAccountType],
  );
  const selectedAccount = compatibleAccounts.some(
    (item) => item.id === accountId,
  )
    ? accountId
    : compatibleAccounts[0]?.id || "";
  const expectedFileCount =
    selectedProvider === "bybit_spot_csv_bundle" ? 4 : 1;
  const mutation = useMutation({
    mutationFn: async () => {
      if (!descriptor) throw new Error("Формат импорта недоступен");
      if (!selectedAccount)
        throw new Error("Сначала создайте или выберите счёт");
      if (files.length !== expectedFileCount)
        throw new Error(`Выберите файлов: ${expectedFileCount}`);
      return uploadImport({
        portfolio_id: portfolioId,
        account_id: selectedAccount,
        source_provider: selectedProvider,
        declared_format: descriptor.supported_file_formats[0]!,
        file: files,
      });
    },
    onSuccess: (result) =>
      navigate(`/p/${portfolioId}/imports/${result.batch.id}`),
  });
  const createAccount = useMutation({
    mutationFn: async () =>
      unwrap(
        await api.POST("/api/v1/portfolios/{portfolio_id}/accounts", {
          params: { path: { portfolio_id: portfolioId } },
          body: {
            name: newAccountName,
            account_type: expectedAccountType,
            institution_name: institutionName || null,
          },
        }),
      ),
    onSuccess: async (account) => {
      setAccountId(account.id);
      setShowAccountForm(false);
      await queryClient.invalidateQueries({
        queryKey: ["accounts", portfolioId],
      });
    },
  });
  if (
    accounts.isLoading ||
    formats.isLoading ||
    !accounts.data ||
    !formats.data
  )
    return <QueryState error={accounts.error ?? formats.error} />;
  const submit = (event: FormEvent) => {
    event.preventDefault();
    mutation.mutate();
  };
  return (
    <div className="page import-start-page">
      <PageHeader title="Новый импорт" />
      <ImportStepper active={1} />
      <form onSubmit={submit} className="import-layout">
        <section className="provider-grid">
          {publicFormats.map((item) => {
            const copy = providerLabels[item.format_id] ?? {
              title: item.format_id,
              description: item.version,
            };
            return (
              <button
                type="button"
                key={item.format_id}
                aria-pressed={selectedProvider === item.format_id}
                onClick={() => {
                  setProvider(item.format_id);
                  setAccountId("");
                  setNewAccountName(copy.title);
                  setInstitutionName(copy.title);
                  setFiles([]);
                }}
                className="provider-option"
              >
                <FileSpreadsheet />
                <span>
                  <strong>{copy.title}</strong>
                  <small>{copy.description}</small>
                  <em>
                    {item.supported_file_formats
                      .map((format) => format.toUpperCase())
                      .join(", ")}
                  </em>
                </span>
                {selectedProvider === item.format_id ? <FileCheck2 /> : null}
              </button>
            );
          })}
        </section>
        <aside className="upload-panel">
          <h2>Отчёт и счёт</h2>
          <div className="upload-account">
            <label>
              Целевой счёт
              <select
                value={selectedAccount}
                onChange={(e) => setAccountId(e.target.value)}
              >
                {!compatibleAccounts.length ? (
                  <option value="">Создайте подходящий счёт</option>
                ) : null}
                {compatibleAccounts.map((item) => (
                  <option key={item.id} value={item.id}>
                    {userFacingName(item.name)} ·{" "}
                    {userFacingName(
                      item.institution_name ||
                        (item.account_type === "cex"
                          ? "Криптобиржа"
                          : "Брокер"),
                    )}
                  </option>
                ))}
              </select>
            </label>
            <Button
              type="button"
              variant="ghost"
              onClick={() => setShowAccountForm((value) => !value)}
            >
              + Добавить брокерский счёт или счёт криптобиржи
            </Button>
            {showAccountForm || !compatibleAccounts.length ? (
              <div className="inline-account-form">
                <label>
                  Название
                  <input
                    value={newAccountName}
                    onChange={(event) => setNewAccountName(event.target.value)}
                  />
                </label>
                <div className="field-row">
                  <label>
                    Тип
                    <select value={expectedAccountType} disabled>
                      <option value="broker">Брокер</option>
                      <option value="cex">Криптобиржа</option>
                    </select>
                  </label>
                  <label>
                    Организация
                    <input
                      value={institutionName}
                      onChange={(event) =>
                        setInstitutionName(event.target.value)
                      }
                      placeholder="Например, Т‑Инвестиции"
                    />
                  </label>
                </div>
                <Button
                  type="button"
                  variant="secondary"
                  disabled={createAccount.isPending || !newAccountName.trim()}
                  onClick={() => createAccount.mutate()}
                >
                  Создать счёт
                </Button>
              </div>
            ) : null}
            <small>
              Для каждого брокера или биржи используйте отдельный счёт.
            </small>
          </div>
          <label
            className="dropzone"
            onDragOver={(event) => event.preventDefault()}
            onDrop={(event) => {
              event.preventDefault();
              setFiles(Array.from(event.dataTransfer.files));
            }}
          >
            <UploadCloud />
            <strong>
              {files.length
                ? `Выбрано файлов: ${files.length}`
                : "Перетащите или выберите файл"}
            </strong>
            <small>
              {selectedProvider === "bybit_spot_csv_bundle"
                ? "Выберите все четыре CSV одновременно"
                : `Ожидается ${(descriptor?.supported_file_formats[0] ?? "файл").toUpperCase()}`}
            </small>
            <input
              type="file"
              aria-label="Файлы отчёта"
              accept={
                selectedProvider === "bybit_spot_csv_bundle"
                  ? ".csv"
                  : selectedProvider === "alfa_broker_xml_import"
                    ? ".xml"
                    : ".xlsx"
              }
              multiple={selectedProvider === "bybit_spot_csv_bundle"}
              onChange={(e) => setFiles(Array.from(e.target.files ?? []))}
            />
          </label>
          <div className="upload-files">
            {selectedProvider !== "bybit_spot_csv_bundle" && files.length ? (
              <ul className="selected-file-list">
                {files.map((file) => (
                  <li key={file.name}>{file.name}</li>
                ))}
              </ul>
            ) : null}
            {selectedProvider === "bybit_spot_csv_bundle" ? (
              <ol className="document-checklist">
                {bybitDocuments.map((document) => {
                  const selectedFile = files.find((file) =>
                    file.name.toLowerCase().includes(document.marker),
                  );
                  return (
                    <li
                      key={document.label}
                      className={selectedFile ? "document-ready" : undefined}
                    >
                      <FileCheck2 size={15} />
                      <span>{document.label}</span>
                      <small title={selectedFile?.name}>
                        {selectedFile?.name ?? "Не выбран"}
                      </small>
                    </li>
                  );
                })}
              </ol>
            ) : null}
            <div className="privacy-note">
              <LockKeyhole size={17} />
              <span>
                Файл отправляется только локальной серверной части и не попадает в
                дерево проекта.
              </span>
            </div>
            {!selectedAccount ? (
              <p role="status">
                Сначала создайте{" "}
                {expectedAccountType === "cex"
                  ? "счёт криптобиржи для Байбит"
                  : "брокерский счёт"}{" "}
                — затем станет доступна загрузка.
              </p>
            ) : files.length !== expectedFileCount ? (
              <p role="status">
                Выберите{" "}
                {expectedFileCount === 4
                  ? "четыре CSV одного экспорта"
                  : "один отчёт"}
                .
              </p>
            ) : null}
            {mutation.error || createAccount.error ? (
              <p className="form-error">
                {formFailure(mutation.error ?? createAccount.error)}
              </p>
            ) : null}
            <Button
              disabled={
                files.length !== expectedFileCount ||
                !selectedAccount ||
                mutation.isPending
              }
            >
              {mutation.isPending ? "Загружаем…" : "Загрузить и проверить"}
            </Button>
          </div>
        </aside>
      </form>
    </div>
  );
}
