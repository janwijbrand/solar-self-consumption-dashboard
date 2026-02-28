<script setup>
import { computed } from 'vue'

const props = defineProps({
  data:     Object,
  forecast: Array,
})

const grouped = computed(() => {
  if (!props.forecast?.length) return []
  const result = []
  for (let i = 0; i < props.forecast.length; i += 4) {
    const slice = props.forecast.slice(i, i + 4)
    result.push({
      hour:          slice[0].hour,
      potential_pct: Math.max(...slice.map(h => h.potential_pct)),
      is_daytime:    slice.some(h => h.is_daytime),
    })
  }
  return result
})

const state = computed(() => {
  const d = props.data
  if (!d) return null

  const solar  = d.solar_w ?? 0
  const exp    = d.grid_export_w ?? 0
  const imp    = d.grid_import_w ?? 0

  if (solar < 50) return {
    level: 'none',
    icon: '🌑',
    label: 'No solar production',
    detail: 'Avoid running high-load appliances',
    bg: 'bg-gray-900',
    border: 'border-gray-800',
    text: 'text-gray-400',
    badge: 'bg-gray-800 text-gray-500',
  }

  if (exp > 100) return {
    level: 'surplus',
    icon: '☀️',
    label: 'Great time to run appliances',
    detail: `Exporting ${fmt(exp)} surplus to grid`,
    bg: 'bg-emerald-950',
    border: 'border-emerald-700',
    text: 'text-emerald-300',
    badge: 'bg-emerald-900 text-emerald-300',
  }

  if (solar > 100 && imp < 300) return {
    level: 'balanced',
    icon: '⛅',
    label: 'Solar covering most needs',
    detail: `Producing ${fmt(solar)}, drawing ${fmt(imp)} from grid`,
    bg: 'bg-amber-950',
    border: 'border-amber-700',
    text: 'text-amber-300',
    badge: 'bg-amber-900 text-amber-300',
  }

  return {
    level: 'limited',
    icon: '🌤',
    label: 'Limited solar — mostly grid',
    detail: `Producing ${fmt(solar)}, drawing ${fmt(imp)} from grid`,
    bg: 'bg-orange-950',
    border: 'border-orange-800',
    text: 'text-orange-300',
    badge: 'bg-orange-900 text-orange-300',
  }
})

function fmt(w) {
  if (w === null || w === undefined) return '—'
  return w >= 1000 ? (w / 1000).toFixed(1) + ' kW' : Math.round(w) + ' W'
}

function barColor(h) {
  if (!h.is_daytime)        return 'bg-gray-900 ring-1 ring-gray-700'
  if (h.potential_pct >= 60) return 'bg-amber-400'
  if (h.potential_pct >= 30) return 'bg-amber-700'
  if (h.potential_pct >= 10) return 'bg-gray-600'
  return 'bg-gray-700'
}

function transition(hours, i) {
  if (i === 0 || !hours) return null
  const prev = hours[i - 1]
  const curr = hours[i]
  if (!prev.is_daytime && curr.is_daytime) return 'rise'
  if (prev.is_daytime  && !curr.is_daytime) return 'set'
  return null
}
</script>

<template>
  <div
    v-if="state"
    :class="[state.bg, 'border', state.border, 'rounded-2xl', 'px-5', 'py-4', 'flex', 'flex-col', 'gap-3']"
  >
    <div class="flex items-center gap-4">
      <span class="text-3xl leading-none">{{ state.icon }}</span>
      <div class="flex-1 min-w-0">
        <div :class="[state.text, 'font-semibold', 'text-base', 'leading-snug']">
          {{ state.label }}
        </div>
        <div class="text-gray-500 text-sm mt-0.5">{{ state.detail }}</div>
      </div>
    </div>

    <!-- Mobile: 6 grouped bars (4 h each) -->
    <div v-if="grouped.length" class="flex gap-1 md:hidden">
      <div
        v-for="h in grouped"
        :key="h.hour"
        class="flex-1 flex flex-col items-center gap-0.5"
      >
        <div :class="[barColor(h), 'w-full h-6 rounded']" />
        <span class="text-xs text-gray-600 leading-none">{{ h.hour }}</span>
      </div>
    </div>

    <!-- md+: 24 hourly bars -->
    <div v-if="forecast?.length" class="hidden md:flex gap-1">
      <div
        v-for="(h, i) in forecast"
        :key="h.hour"
        class="flex-1 flex flex-col items-center gap-0.5"
      >
        <div :class="[barColor(h), 'w-full h-6 rounded flex items-center justify-center']">
          <span v-if="transition(forecast, i) === 'rise'" class="text-xs leading-none">☀</span>
          <span v-else-if="transition(forecast, i) === 'set'" class="text-xs leading-none">🌙</span>
        </div>
        <span class="text-xs text-gray-600 leading-none">{{ parseInt(h.hour) % 6 === 0 ? h.hour : '' }}</span>
      </div>
    </div>
  </div>
  <div v-else class="bg-gray-900 rounded-2xl px-5 py-4 text-gray-600 text-sm">
    Loading…
  </div>
</template>
