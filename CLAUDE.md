# Kontekst projektu (dla Claude Code)
- Użytkownik pisze po polsku; odpowiadaj po polsku, zwięźle i konkretnie.
- Przy poprawianiu kodu zawsze podawaj PEŁNY plik, nie fragmenty.
- Skrypty AE to ExtendScript (ES3): bez let/const/=>, bez JSON.parse (używamy eval), bez Array.map.
- Nie mamy dostępu do AE z poziomu agenta – zmiany weryfikuj `node --check` i testami wyrażeń w Node; zaznaczaj, że wymagają testu w AE.
- Wyrażenia AE: czas w kompozycjach wspólnych = (sekundy od startu aktywności) × K (K z komentarza HRF_DATA).
- Dane z GPS użytkownika są prywatne: nie commituj `data/*.json`.
- Przeczytaj README.md – opis przepływu i decyzji.
