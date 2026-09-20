const STYLES = {
  Strong: 'bg-green-ok-bg text-green-ok',
  Average: 'bg-amber-bg text-amber',
  Weak: 'bg-red-bg text-red',
}

export default function Badge({ category }) {
  return (
    <span className={`inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-[11.5px] font-bold tracking-wide ${STYLES[category] || STYLES.Average}`}>
      {category}
    </span>
  )
}
