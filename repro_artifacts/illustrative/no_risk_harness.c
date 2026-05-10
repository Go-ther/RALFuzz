#include "cJSON.h"
#include <stdio.h>
#include <string.h>

int main(void) {
    const char *input = "{\"key\":\"value\"}";
    cJSON *json = cJSON_Parse(input);
    if (json) {
        printf("Parsed successfully\n");
        cJSON_Delete(json);
    } else {
        printf("Parse failed\n");
    }
    return 0;
}
