import { chromium } from 'playwright'
import { mkdir } from 'node:fs/promises'
import path from 'node:path'

const outputDir = path.resolve('artifacts')
await mkdir(outputDir, { recursive: true })

const browser = await chromium.launch({
  executablePath: 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
  headless: true,
})

const errors = []
const checks = []

async function checkViewport(name, viewport) {
  const page = await browser.newPage({ viewport })
  page.on('console', (message) => {
    if (message.type() === 'error') errors.push(`${name}: ${message.text()}`)
  })
  page.on('pageerror', (error) => errors.push(`${name}: ${error.message}`))
  await page.goto('http://127.0.0.1:5173', { waitUntil: 'networkidle' })
  await page.waitForSelector('.graph-node circle', { timeout: 15000 })
  const initial = await page.evaluate(() => ({
    width: document.documentElement.scrollWidth,
    viewport: document.documentElement.clientWidth,
    graphNodes: document.querySelectorAll('.graph-node circle').length,
    graphBox: document.querySelector('.graph-stage')?.getBoundingClientRect().toJSON(),
    chatBox: document.querySelector('.chat-panel')?.getBoundingClientRect().toJSON(),
  }))
  checks.push({ name, phase: 'initial', ...initial })
  await page.getByRole('button', { name: /Why are customers experiencing call drops/i }).click()
  await page.waitForSelector('.answer-card', { timeout: 15000 })
  await page.waitForTimeout(1200)
  const answered = await page.evaluate(() => ({
    graphNodes: document.querySelectorAll('.graph-node circle').length,
    answers: document.querySelectorAll('.answer-card').length,
    hasTraversalTab: [...document.querySelectorAll('.answer-tabs button')].some((item) => item.textContent.includes('Traversal')),
    hasHorizontalOverflow: document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
  }))
  checks.push({ name, phase: 'answered', ...answered })
  await page.screenshot({ path: path.join(outputDir, `${name}.png`), fullPage: true })
  await page.close()
}

await checkViewport('desktop-1440x900', { width: 1440, height: 900 })
await checkViewport('mobile-390x844', { width: 390, height: 844 })
await browser.close()

const failed = checks.some((check) =>
  check.graphNodes < 1 || check.hasHorizontalOverflow === true ||
  (check.phase === 'answered' && (!check.answers || !check.hasTraversalTab))
)

console.log(JSON.stringify({ status: failed || errors.length ? 'FAIL' : 'PASS', checks, errors }, null, 2))
if (failed || errors.length) process.exit(1)
