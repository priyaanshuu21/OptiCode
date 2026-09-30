/* E-SEM-012 and E-SEM-013: break and continue only make sense inside a loop. */

int main() {
    int total = 0;
    if (total == 0) {
        break;
    }
    total = total + 1;
    continue;
    return total;
}
