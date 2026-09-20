import { motion } from 'framer-motion'

export default function ProgressRing({ percent, statusText }) {
  return (
    <div className="relative flex items-center justify-center min-h-[460px]">
      <div className="relative w-[min(400px,84vw)] aspect-square">
        <div className="absolute inset-0 rounded-full border border-ink bg-white overflow-hidden">
          <motion.div
            className="absolute left-0 right-0 bottom-0 bg-lime"
            initial={{ height: '0%' }}
            animate={{ height: `${percent}%` }}
            transition={{ duration: 1.9, ease: [0.22, 0.9, 0.3, 1] }}
          >
            <svg viewBox="0 0 200 20" preserveAspectRatio="none" className="absolute -top-px left-0 w-[200%] h-5 animate-[wave_5.2s_linear_infinite]">
              <path d="M0 10 Q 25 0 50 10 T 100 10 T 150 10 T 200 10 V20 H0 Z" fill="rgba(11,15,13,0.15)" />
            </svg>
          </motion.div>
        </div>

        <div className="absolute inset-0 flex items-center justify-center z-[2] serif font-semibold text-[clamp(56px,9vw,84px)]">
          {Math.round(percent)}
          <span className="text-[0.5em] ml-0.5 font-medium text-sage">%</span>
        </div>

        <div className="absolute -bottom-[68px] left-1/2 -translate-x-1/2 text-center w-[340px] max-sm:static max-sm:translate-x-0 max-sm:mt-7 max-sm:w-auto">
          <div className="flex items-center justify-center gap-2 text-[15px] font-semibold">
            <motion.svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2.4"
              strokeLinecap="round"
              strokeLinejoin="round"
              className="w-[15px] h-[15px]"
              animate={{ rotate: 360 }}
              transition={{ duration: 3, repeat: Infinity, ease: 'linear' }}
            >
              <path d="M12 3v3M12 18v3M4.2 4.2l2.1 2.1M17.7 17.7l2.1 2.1M3 12h3M18 12h3M4.2 19.8l2.1-2.1M17.7 6.3l2.1-2.1" />
            </motion.svg>
            <span>{statusText}</span>
          </div>
          <div className="mt-1.5 text-[12.5px] text-sage">
            This may take a few moments. Please don't close this window.
          </div>
        </div>
      </div>
    </div>
  )
}
