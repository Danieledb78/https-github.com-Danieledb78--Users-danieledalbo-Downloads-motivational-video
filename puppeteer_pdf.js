// puppeteer_pdf.js — Conversione HTML -> PDF via headless Chromium (AIOS v2.0)
// Uso: node puppeteer_pdf.js <input.html> <output.pdf>
// Garantisce qualità tipografica professionale (CSS, font, layout) richiesta per i
// lead magnet PDF di ACM&Partners — vietato generare con reportlab/fpdf puri.

const puppeteer = require("puppeteer");
const path = require("path");

async function generatePDF(inputHtml, outputPdf) {
  const browser = await puppeteer.launch({
    args: ["--no-sandbox", "--disable-setuid-sandbox"],  // obbligatorio su Replit
  });
  try {
    const page = await browser.newPage();
    const htmlPath = path.resolve(inputHtml);
    await page.goto(`file://${htmlPath}`, { waitUntil: "networkidle0" });
    await page.pdf({
      path: outputPdf,
      format: "A4",
      printBackground: true,
      margin: { top: "0mm", right: "0mm", bottom: "0mm", left: "0mm" },
    });
  } finally {
    await browser.close();
  }
}

const [, , input, output] = process.argv;
if (!input || !output) {
  console.error("Uso: node puppeteer_pdf.js <input.html> <output.pdf>");
  process.exit(1);
}
generatePDF(input, output)
  .then(() => console.log(`PDF generato: ${output}`))
  .catch((err) => { console.error(err); process.exit(1); });
