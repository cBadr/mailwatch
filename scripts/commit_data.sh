#!/usr/bin/env bash
# حفظ التغييرات (البيانات والإعدادات) في Git مع إعادة المحاولة عند التعارض.
#   - وضع المستودعين: DATA_REPO_DIR=data_repo → يحفظ كل شيء داخل مستودع البيانات الخاص
#   - مستودع واحد: يضيف data/ و config/ في نفس المستودع
set -euo pipefail

DATA_DIR="${DATA_DIR:-data}"
MSG="${1:-تحديث البيانات $(date -u +'%Y-%m-%d %H:%M UTC')}"
GIT_ID=(-c user.name="mailwatch[bot]" -c user.email="mailwatch@users.noreply.github.com")

if [ -n "${DATA_REPO_DIR:-}" ] && [ -d "$DATA_REPO_DIR/.git" ]; then
  cd "$DATA_REPO_DIR"
  git add -A
elif [ -d "$DATA_DIR/.git" ]; then
  cd "$DATA_DIR"
  git add -A
else
  git add -A -- "$DATA_DIR" config 2>/dev/null || git add -A -- "$DATA_DIR"
fi

if git diff --cached --quiet; then
  echo "لا توجد تغييرات لحفظها"
  exit 0
fi

git "${GIT_ID[@]}" commit -q -m "$MSG"
echo "تم إنشاء commit"

for attempt in 1 2 3 4 5; do
  if git push -q; then
    echo "تم الرفع"
    exit 0
  fi
  echo "فشل الرفع (محاولة $attempt)، مزامنة ثم إعادة المحاولة..."
  git "${GIT_ID[@]}" pull --rebase -q -X theirs || true
  sleep $((attempt * 3))
done

echo "تعذر رفع التغييرات بعد عدة محاولات" >&2
exit 1
