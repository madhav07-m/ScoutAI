import { motion } from 'framer-motion'
import Badge from './Badge'
import GapCard from './GapCard'

export default function Results({ data, sessionId, geminiKey, readOnly, onStartOver, onGapUpdated }) {
  const { ranked, counts, low_extraction_docs, ocr_used_docs, has_gemini_key, gemini_error, gap_status, viewedAt } = data

  return (
    <section className="relative z-[2] max-w-[1120px] mx-auto px-5 sm:px-14 pb-24">
      <div className="flex items-baseline justify-between mb-2 flex-wrap gap-2">
        <h2 className="serif text-[30px] font-semibold">Ranking results</h2>
        <button
          onClick={onStartOver}
          className="inline-flex items-center gap-1.5 px-4 py-2 rounded-full border border-ink text-[13.5px] font-semibold hover:bg-ink hover:text-paper transition-colors"
        >
          ↺ Start over
        </button>
      </div>
      <p className="text-[13px] text-sage mb-7 max-w-[640px] leading-relaxed">
        Fit Score = embedding-based cosine similarity to the JD (0–100). LLM Score = Gemini's independent
        judgment, shown when a Gemini key is provided — the two can genuinely disagree.
      </p>

      <div className="space-y-3 mb-4">
        {viewedAt && (
          <div className="text-[13px] px-4 py-3 border-l-2 border-ink bg-paper-2 text-[#3D423E]">
            📁 Viewing a saved analysis from {viewedAt}. Since the original ranking session has expired, gap
            analysis can't be generated or regenerated here — start a new analysis for that.
          </div>
        )}
        {low_extraction_docs?.length > 0 && (
          <div className="text-[13px] px-4 py-3 border-l-2 border-amber bg-amber-bg text-amber">
            ⚠️ Very little text was extracted from: {low_extraction_docs.join(', ')}. A low fit score for these
            may reflect a parsing issue rather than a weak match.
          </div>
        )}
        {ocr_used_docs?.length > 0 && (
          <div className="text-[13px] px-4 py-3 border-l-2 border-ink bg-paper-2 text-[#3D423E]">
            🔎 OCR fallback was used for: {ocr_used_docs.join(', ')} — worth a quick manual check for
            recognition errors.
          </div>
        )}
        {!has_gemini_key && (
          <div className="text-[13px] px-4 py-3 border-l-2 border-ink bg-paper-2 text-[#3D423E]">
            Add a Gemini API key above to get LLM-assessed scores and gap analysis.
          </div>
        )}
        {gemini_error && (
          <div className="text-[13px] px-4 py-3 border-l-2 border-amber bg-amber-bg text-amber">
            ⚠️ Gemini setup failed: {gemini_error}
          </div>
        )}
        {(gap_status === 'pending' || gap_status === 'running') && (
          <div className="text-[13px] px-4 py-3 border-l-2 border-ink bg-paper-2 text-[#3D423E] flex items-center gap-2">
            <span className="inline-block w-3 h-3 border-2 border-ink border-t-transparent rounded-full animate-spin" />
            Gap analysis is still running in the background — LLM scores and reports below will fill in as
            each resume finishes.
          </div>
        )}
      </div>

      <div className="flex border-t border-ink border-b border-line mb-9">
        <div className="flex-1 px-5 py-4 border-r border-line">
          <div className="serif text-[34px] font-semibold text-green-ok">{counts.Strong}</div>
          <div className="text-xs text-sage mt-0.5">Strong matches</div>
        </div>
        <div className="flex-1 px-5 py-4 border-r border-line">
          <div className="serif text-[34px] font-semibold">{counts.Average}</div>
          <div className="text-xs text-sage mt-0.5">Average matches</div>
        </div>
        <div className="flex-1 px-5 py-4">
          <div className="serif text-[34px] font-semibold text-red">{counts.Weak}</div>
          <div className="text-xs text-sage mt-0.5">Weak matches</div>
        </div>
      </div>

      <table className="w-full border-collapse bg-white border border-ink mb-10">
        <thead>
          <tr>
            {['Resume', 'Fit score', 'Match', 'LLM score'].map((h) => (
              <th key={h} className="text-left px-4.5 py-3 bg-ink text-paper font-semibold text-[11px] uppercase tracking-wider">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {ranked.map((row) => (
            <tr key={row.doc_name} className="border-b border-line last:border-b-0 hover:bg-paper transition-colors">
              <td className="px-4.5 py-3.5 text-[13.5px]">{row.doc_name}</td>
              <td className="px-4.5 py-3.5 text-[13.5px]">{row.fit_score}</td>
              <td className="px-4.5 py-3.5 text-[13.5px]"><Badge category={row.match_category} /></td>
              <td className="px-4.5 py-3.5 text-[13.5px]">{row.llm_score ?? '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <h3 className="serif text-[20px] font-semibold mb-4">
        Gap analysis{' '}
        <span className="text-sage font-normal text-sm font-sans">
          (Gemini, grounded in matched sections)
        </span>
      </h3>
      <motion.div initial="hidden" animate="show" variants={{ show: { transition: { staggerChildren: 0.04 } } }}>
        {ranked.map((row) => (
          <motion.div
            key={row.doc_name}
            variants={{ hidden: { opacity: 0, y: 8 }, show: { opacity: 1, y: 0 } }}
          >
            <GapCard
              row={row}
              sessionId={sessionId}
              geminiKey={geminiKey}
              readOnly={readOnly}
              onUpdated={onGapUpdated}
            />
          </motion.div>
        ))}
      </motion.div>
    </section>
  )
}