import { useEffect, useMemo, useRef, useState } from 'react'
import { MonacoEditorReactComp } from '@typefox/monaco-editor-react'
import type { WrapperConfig } from 'monaco-editor-wrapper'
import { LogLevel } from '@codingame/monaco-vscode-api'
// The same call the wrapper uses for its own model. It registers a *file* as
// well as a model; monaco.editor.createModel does not, which is why a
// hand-made model produced "Unable to resolve nonexistent file".
import { createModelReference } from '@codingame/monaco-vscode-api/monaco'
import * as monaco from 'monaco-editor'
// TextMate grammars. The wrapper runs in 'extended' mode, which enables the
// VSCode TextMate tokenizer and *not* Monarch -- so setMonarchTokensProvider is
// a silent no-op here and these extensions are the supported route to syntax
// colouring. Excluded from Vite's prebundle in vite.config.ts, which is what
// previously OOM'd esbuild.
import '@codingame/monaco-vscode-python-default-extension'
import '@codingame/monaco-vscode-javascript-default-extension'
import '@codingame/monaco-vscode-typescript-basics-default-extension'
// TextMate produces *scopes*; a theme maps them to colours. Without this the
// grammars load and every token still renders as default text -- and the
// console fills with 404s for the missing theme files.
import '@codingame/monaco-vscode-theme-defaults-default-extension'
import { monacoDidLoad } from '../theme'
import type { Language } from '../api'

/**
 * Register the languages up front.
 *
 * The default-extension packages did not register them in this setup, which
 * left every model at languageId "plaintext" -- so the language client's
 * documentSelector never matched, textDocument/didOpen was never sent, and the
 * server sat idle after a perfectly successful handshake. Verified in-browser.
 */
let languagesRegistered = false
function registerLanguages(): void {
  if (languagesRegistered) return
  languagesRegistered = true
  // These must be registered by hand. The default-extension packages imported
  // above do not contribute their languages in this setup -- with them alone,
  // models resolve to "plaintext", the language client's documentSelector never
  // matches, and no diagnostics arrive at all. Correct language identification
  // matters more than colour.
  monaco.languages.register({ id: 'python', extensions: ['.py'], aliases: ['Python'] })
  monaco.languages.register({ id: 'javascript', extensions: ['.js'], aliases: ['JavaScript'] })
  monaco.languages.register({ id: 'typescript', extensions: ['.ts'], aliases: ['TypeScript'] })
  monaco.languages.register({ id: 'go', extensions: ['.go'], aliases: ['Go'] })
}

const LSP_LANGUAGE_ID: Record<Language, string> = {
  python: 'python',
  javascript: 'javascript',
  typescript: 'typescript',
  go: 'go',
}

const FILE_NAME: Record<Language, string> = {
  python: 'solution.py',
  javascript: 'solution.js',
  typescript: 'solution.ts',
  go: 'solution.go',
}

//: One typescript-language-server backs both JavaScript and TypeScript, so the
//: two share a connection; only the document's languageId differs.
const LSP_ENDPOINT: Record<Language, string> = {
  python: 'python',
  javascript: 'typescript',
  typescript: 'typescript',
  go: 'go',
}

function websocketUrl(endpoint: string): string {
  const protocol = location.protocol === 'https:' ? 'wss' : 'ws'
  return `${protocol}://${location.host}/lsp/${endpoint}`
}

/** Cancellation is how a superseded async open reports itself. */
function isCancellation(err: unknown): boolean {
  const name = (err as { name?: string } | null)?.name
  const message = (err as { message?: string } | null)?.message ?? ''
  return name === 'Canceled' || name === 'CodeExpectedError' || message === 'Canceled'
}

function modelUri(language: Language): monaco.Uri {
  return monaco.Uri.parse(`file:///workspace/${FILE_NAME[language]}`)
}

/**
 * Monaco wired to real language servers over WebSockets.
 *
 * Switching language does **not** remount: monaco-vscode-api cannot be
 * initialised twice in one document, and remounting left a blank editor.
 * Instead one editor holds a model per language, and every language server the
 * session may need is connected at mount. Each client's documentSelector picks
 * up the documents it owns.
 */
export function EditorPane({
  language, languages, value, onChange, onCursor, onReader, theme,
}: {
  language: Language
  languages: Language[]
  value: string
  onChange: (text: string) => void
  onCursor: (line: number, column: number) => void
  /** Hands the caller a way to read the live buffer, so Run cannot execute a
   *  stale copy of the code the candidate is looking at. */
  onReader?: (read: (() => string) | null) => void
  /** Seeds the editor's colour theme; later changes go through theme.ts. */
  theme?: 'light' | 'dark'
}) {
  const [lspDown, setLspDown] = useState(false)
  //: The editor arrives asynchronously. Without this in the swap effect's deps,
  //: a language change made before onLoad ran was silently dropped -- the
  //: effect returned early and never re-ran.
  const [ready, setReady] = useState(false)
  const latest = useRef(onChange)
  latest.current = onChange

  const editorRef = useRef<monaco.editor.IStandaloneCodeEditor | null>(null)
  const models = useRef(new Map<Language, monaco.editor.ITextModel>())
  //: Model *references* must be held and disposed. Dropping the reference while
  //: keeping the model is what leaves the file service holding a phantom entry.
  const refs = useRef<{ dispose: () => void }[]>([])
  const pending = useRef(new Map<Language, Promise<monaco.editor.ITextModel>>())
  //: The language most recently asked for. Model creation is asynchronous, so a
  //: switch that resolves late must not overwrite a newer one -- swapping
  //: python -> ts -> js -> python left TypeScript on screen.
  const desired = useRef<Language>(language)
  //: Which language's model is actually attached to the editor right now.
  //: `value` changes as soon as the language does, but the model swap is
  //: asynchronous -- so without this the value effect writes the incoming
  //: language's scaffold into the *outgoing* language's buffer.
  const attached = useRef<Language>(language)
  const subs = useRef<monaco.IDisposable[]>([])
  const seed = useRef({ language, value })
  //: What the editor itself last produced. Without this, every keystroke looks
  //: like an external change and setValue() fires, resetting the cursor to the
  //: start -- which typed text out backwards.
  const echoed = useRef(value)

  registerLanguages()

  // Connect every server this session might need, once. Reconnecting on switch
  // would mean tearing down the wrapper, which is exactly what breaks.
  const endpoints = useMemo(
    () => [...new Set(languages.map((l) => LSP_ENDPOINT[l]))],
    [languages.join(',')], // eslint-disable-line react-hooks/exhaustive-deps
  )

  const config: WrapperConfig = useMemo(() => ({
    $type: 'extended',
    logLevel: LogLevel.Warning,
    vscodeApiConfig: {
      userConfiguration: {
        json: JSON.stringify({
          'editor.fontSize': 13,
          'editor.minimap.enabled': false,
          'editor.tabSize': 4,
          'editor.renderWhitespace': 'selection',
          'editor.bracketPairColorization.enabled': true,
          'workbench.colorTheme': theme === 'dark'
            ? 'Default Dark Modern'
            : 'Default Light Modern',
        }),
      },
    },
    editorAppConfig: {
      editorOptions: {
        // Hover and signature-help widgets are otherwise rendered inside the
        // editor container, so in this narrow pane they were clipped and ran
        // off the right of the screen. This puts them in a viewport-fixed
        // overlay that repositions to stay visible.
        fixedOverflowWidgets: true,
      },
      codeResources: {
        modified: {
          // Must be a real file:// URI, and the language must be stated
          // explicitly: without a resolvable languageId the client never sends
          // textDocument/didOpen and no diagnostics ever arrive.
          text: seed.current.value,
          uri: `file:///workspace/${FILE_NAME[seed.current.language]}`,
          enforceLanguageId: LSP_LANGUAGE_ID[seed.current.language],
        },
      },
    },
    languageClientConfigs: {
      configs: Object.fromEntries(
        endpoints.map((endpoint) => [
          endpoint,
          {
            connection: {
              options: {
                $type: 'WebSocketUrl',
                url: websocketUrl(endpoint),
                startOptions: { onCall: () => setLspDown(false), reportStatus: true },
                stopOptions: { onCall: () => undefined, reportStatus: false },
              },
            },
            clientOptions: {
              documentSelector:
                endpoint === 'typescript' ? ['javascript', 'typescript'] : [endpoint],
            },
          },
        ]),
      ),
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }), [endpoints.join(',')])

  /** Every model whose edits we are already listening to. */
  const watched = useRef(new WeakSet<monaco.editor.ITextModel>())

  function watch(model: monaco.editor.ITextModel): void {
    // The wrapper creates the first model itself, so registering a model is not
    // the same as listening to it. Missing this listener meant edits never
    // reached the store, and the value effect then overwrote them.
    if (watched.current.has(model)) return
    watched.current.add(model)
    subs.current.push(
      model.onDidChangeContent(() => {
        if (editorRef.current?.getModel() !== model) return
        const text = model.getValue()
        echoed.current = text
        latest.current(text)
      }),
    )
  }

  /**
   * Model for `lang`, created on first use.
   *
   * Uses createModelReference rather than monaco.editor.createModel because the
   * latter creates a model with no file behind it. Everything works until
   * something resolves that URI -- the language client opening the document, or
   * the editor service -- at which point the file service throws "Unable to
   * resolve nonexistent file '/workspace/solution.ts'". This is exactly how the
   * wrapper builds its own initial model.
   */
  async function modelFor(
    lang: Language, text: string,
  ): Promise<monaco.editor.ITextModel> {
    const existing = models.current.get(lang)
    if (existing && !existing.isDisposed()) {
      watch(existing)
      return existing
    }

    // A language switched to twice in quick succession must not race itself
    // into two references for one URI.
    const inFlight = pending.current.get(lang)
    if (inFlight) return inFlight

    const create = (async () => {
      try {
        const ref = await createModelReference(modelUri(lang), text)
        refs.current.push(ref)
        const model = ref.object.textEditorModel
        if (model === null) throw new Error(`no model for ${lang}`)
        if (model.getLanguageId() !== LSP_LANGUAGE_ID[lang]) {
          ref.object.setLanguageId(LSP_LANGUAGE_ID[lang])
        }
        watch(model)
        models.current.set(lang, model)
        return model
      } finally {
        // Always clear, or a failed open would wedge this language forever.
        pending.current.delete(lang)
      }
    })()

    pending.current.set(lang, create)
    return create
  }

  // Swap models when the language changes, or as soon as the editor exists.
  useEffect(() => {
    const editor = editorRef.current
    if (!editor || !ready) return
    let cancelled = false
    desired.current = language

    void (async () => {
      let model: monaco.editor.ITextModel
      try {
        model = await modelFor(language, value)
      } catch (err) {
        // A superseded switch rejects with a Cancellation, which is expected
        // rather than exceptional; anything else is worth seeing.
        if (!isCancellation(err)) console.warn('could not open the editor buffer:', err)
        return
      }

      // Guard against a stale resolution: only the language still being asked
      // for may take the editor.
      if (cancelled || desired.current !== language) return
      if (editorRef.current !== editor) return
      if (editor.getModel() === model) return

      editor.setModel(model)
    // Each language's model *is* its buffer, and it keeps whatever was typed.
    // Only a brand-new model needs seeding from the scaffold; pushing the
    // store's value in here would clobber retained work whenever the store had
    // not yet caught up with the last keystroke.
      if (model.getValue() === '') model.setValue(value)
      const text = model.getValue()
      echoed.current = text
      latest.current(text)
      editor.focus()
    })()

    return () => { cancelled = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [language, ready])

  // Reflect genuinely external changes (a new question, or Reset) without
  // making the editor a controlled component.
  useEffect(() => {
    if (value === echoed.current) return
    // Only write into the buffer this value belongs to. During a language
    // switch the value is already the new language's while the old model is
    // still attached, and writing then corrupts the outgoing buffer -- which
    // showed up as TypeScript appearing in the Python file.
    if (attached.current !== language) return
    const editor = editorRef.current
    const model = editor?.getModel()
    if (!model || model.getValue() === value) {
      echoed.current = value
      return
    }
    // Never overwrite someone mid-keystroke. Text typed before the editor
    // finished loading is not yet in the store, and pushing the stale value
    // back would silently erase it.
    if (editor?.hasTextFocus()) return
    echoed.current = value
    model.setValue(value)
  }, [value, language])

  useEffect(() => () => {
    subs.current.forEach((s) => s.dispose())
    subs.current = []
    // Releasing the references is what lets the file service drop the files.
    refs.current.forEach((ref) => ref.dispose())
    refs.current = []
    onReader?.(null)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return (
    // `data-editor-ready` marks the point where the change listener is
    // attached. Before it, typing lands in the model but never reaches the
    // store, so a Run would execute the scaffold instead of what was typed.
    <div className="relative h-full" data-editor-ready={ready ? 'true' : 'false'}>
      <MonacoEditorReactComp
        wrapperConfig={config}
        style={{ height: '100%' }}
        onError={() => setLspDown(true)}
        onLoad={(wrapper) => {
          if (import.meta.env.DEV) {
            ;(window as unknown as Record<string, unknown>).__wrapper = wrapper
            // Exposed for debugging the language/grammar registry from the
            // browser; see the Phase J notes in docs/container-discovery.md.
            ;(window as unknown as Record<string, unknown>).__monaco = monaco
            // Exposed for debugging the language/grammar registry from the
            // browser; see the Phase J notes in docs/container-discovery.md.
            ;(window as unknown as Record<string, unknown>).__monaco = monaco
          }
          const editor = wrapper.getEditor()
          if (!editor) return
          editorRef.current = editor
          const initial = editor.getModel()
          // The wrapper built this one through the file service already.
          if (initial) {
            models.current.set(seed.current.language, initial)
            attached.current = seed.current.language
            watch(initial)
          }
          // Attach to the language we are actually showing.
          void modelFor(language, value)
            .then((model) => {
              if (desired.current !== language) return
              if (editorRef.current === editor && editor.getModel() !== model) {
                editor.setModel(model)
                attached.current = language
              }
            })
            .catch((err) => {
              if (!isCancellation(err)) {
                console.warn('could not open the editor buffer:', err)
              }
            })
          editor.onDidChangeCursorPosition((e) =>
            onCursor(e.position.lineNumber, e.position.column))
          onReader?.(() => editor.getModel()?.getValue() ?? '')

          // Readiness first: syntax colours and themes are cosmetic, and a
          // failure in either must not leave the editor unusable. An earlier
          // ordering let a throw here strand `ready` at false, so nothing could
          // type into a perfectly working editor.
          setReady(true)
          try {
            monacoDidLoad()
          } catch (err) {
            console.warn('editor theming unavailable:', err)
          }
        }}
      />
      {lspDown && (
        // Editing must keep working without a language server; only the
        // intelligence degrades.
        <div className="absolute right-3 top-2 rounded bg-[var(--color-warn)]/20 px-2 py-1
                        text-xs text-[var(--color-warn)]">
          Language server unavailable — editing still works, hints are off
        </div>
      )}
    </div>
  )
}
