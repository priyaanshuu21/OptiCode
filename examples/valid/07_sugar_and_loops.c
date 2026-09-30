/* Assignment sugar, break, and continue in one loop. */

int count_up_to(int limit) {
    int found = 0;
    for (int i = 0; i < 100; i = i + 1) {
        if (i % 2 == 0) {
            continue;
        }
        found = found + 1;
        if (found == limit) {
            break;
        }
    }
    return found;
}

int main() {
    int total = 1;
    total += 2;
    total += 4;
    total *= 3;
    total -= 1;
    total /= 2;
    printf("after sugar: %d\n", total);

    int counter = 0;
    counter++;
    counter++;
    counter--;
    printf("after increments: %d\n", counter);

    printf("odd numbers below 100: %d\n", count_up_to(5));

    int i = 0;
    while (1) {
        i = i + 1;
        if (i > 3) {
            break;
        }
    }
    printf("escaped while: %d\n", i);

    return 0;
}