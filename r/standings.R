# Example baseballr task: pull division standings for a season.
# Run: Rscript r/standings.R 2025
# Lands Parquet in the same bronze layout as the Python pullers.
#
# Compliance: baseballr is MIT (code); the DATA it fetches is still governed by the
# upstream source's terms (see ../public-data-sources.md). Derived analysis only.

suppressMessages({
  library(baseballr)
  library(arrow)
})

args <- commandArgs(trailingOnly = TRUE)
season <- if (length(args) >= 1) as.integer(args[[1]]) else as.integer(format(Sys.Date(), "%Y"))

message(sprintf("baseballr: standings for %d", season))

# Division ids 201:204 (AL E/C/W) and 205 etc. vary; iterate the common set.
divisions <- c(200, 201, 202, 203, 204, 205)
out <- do.call(rbind, lapply(divisions, function(d) {
  tryCatch(
    bref_standings_on_date(date = sprintf("%d-10-01", season), division = d),
    error = function(e) NULL
  )
}))

dir.create(sprintf("data/bronze/standings/season=%d", season),
           recursive = TRUE, showWarnings = FALSE)
path <- sprintf("data/bronze/standings/season=%d/standings_%d.parquet", season, season)
arrow::write_parquet(out, path)
writeLines(
  "Stats courtesy of Baseball-Reference.com (Sports Reference LLC).",
  sprintf("data/bronze/standings/season=%d/NOTICE.txt", season)
)
message(sprintf("wrote %s", path))
