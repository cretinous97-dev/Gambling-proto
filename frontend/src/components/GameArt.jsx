/**
 * Game artwork and brand mark.
 *
 * The images are generated art, squared/rounded/composited onto the site
 * palette at build time by scripts/optimise_art.py - not in the browser. That
 * keeps a 14MB set of raw PNGs down to ~200KB of WebP, and means the page
 * renders instantly instead of doing canvas work on every visit.
 *
 * Each tile ships WebP with a PNG fallback, so nothing depends on browser
 * WebP support.
 */

/** slug -> generated artwork. `null` means "not generated yet, use the emoji". */
export const GAME_ART = {
  dice: '/games/dice',
  crash: '/games/crash',
  slots: '/games/slots',
  roulette: '/games/roulette',
  blackjack: '/games/blackjack',
  mines: '/games/mines',
  plinko: '/games/plinko',
  wheel: '/games/wheel',
  keno: '/games/keno',
  coinflip: '/games/coinflip',
  limbo: '/games/limbo',
}

/** Emoji fallback, matching the lobby's original look. */
const FALLBACK = {
  dice: '🎲', crash: '🚀', slots: '🎰', roulette: '🎡', blackjack: '🃏',
  mines: '💣', plinko: '🔻', wheel: '🎯', keno: '🔢', coinflip: '🪙', limbo: '📈',
}

export default function GameArt({ slug, size = 46, className = '' }) {
  const base = GAME_ART[slug]
  const style = { width: size, height: size, borderRadius: size * 0.16 }

  if (!base) {
    return (
      <span className={`game-art-fallback ${className}`} style={{ fontSize: size * 0.72 }}>
        {FALLBACK[slug] || '🎮'}
      </span>
    )
  }
  return (
    <picture>
      <source srcSet={`${base}.webp`} type="image/webp" />
      <img
        src={`${base}.png`}
        alt=""
        className={`game-art ${className}`}
        style={style}
        width={size}
        height={size}
        loading="lazy"
        decoding="async"
      />
    </picture>
  )
}

/** The brand mark, same treatment. */
export function BrandMark({ size = 32, className = '' }) {
  return (
    <picture>
      <source srcSet="/brand/logo.webp" type="image/webp" />
      <img
        src="/brand/logo.png"
        alt=""
        className={`brand-mark-img ${className}`}
        style={{ width: size, height: size, borderRadius: size * 0.26 }}
        width={size}
        height={size}
        decoding="async"
      />
    </picture>
  )
}
