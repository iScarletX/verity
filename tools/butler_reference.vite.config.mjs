import path from 'node:path'

const butlerRoot = process.env.VERITY_BUTLER_ROOT
const entry = process.env.VERITY_BUTLER_ENTRY
const outputDirectory = process.env.VERITY_BUTLER_BUNDLE_OUTPUT

if (!butlerRoot || !entry || !outputDirectory) {
  throw new Error('Butler reference build environment is incomplete')
}

export default {
  root: butlerRoot,
  resolve: {
    alias: {
      '@butler': path.join(butlerRoot, 'src'),
    },
  },
  build: {
    ssr: entry,
    outDir: outputDirectory,
    emptyOutDir: true,
    minify: false,
    sourcemap: false,
    rollupOptions: {
      output: {
        entryFileNames: 'butler-reference-runner.mjs',
      },
    },
  },
  ssr: {
    noExternal: true,
  },
}
